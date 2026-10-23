from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.domain import AuthError, GoneError
from app.dependencies import get_db
from app.main import app
from app.modules.identity.application.schemas.request import (
    ChangePasswordRequest, LoginRequest, RegisterRequest, RequestEmailChangeRequest,
)
from app.modules.identity.application.usecases.auth_usecase import AuthUseCase
from app.modules.identity.application.usecases.user_usecase import UserUseCase
from app.modules.identity.domain.aggregates import User
from app.modules.identity.domain.entities.account_deletion_token import AccountDeletionToken
from app.modules.identity.domain.entities.email_change_token import EmailChangeToken
from app.modules.identity.domain.entities.refresh_token import RefreshToken
from app.modules.identity.infrastructure.repositories import UserRepository
from app.modules.identity.infrastructure.services import TokenService
from app.outbox.models import Event

pytestmark = pytest.mark.integration


async def _buyer(session):
    await UserUseCase(session).register(RegisterRequest(
        name="Comprador Demo", cpf="52998224725", email="buyer@example.com", password="senha-anterior-123",
    ))
    return await UserRepository(session).find_by("email", "buyer@example.com")


async def _request_token(session, user, kind):
    usecase = UserUseCase(session)
    if kind == "email":
        await usecase.request_email_change(user, RequestEmailChangeRequest(new_email="new@example.com"))
        event_type = "EmailChangeRequested"
    else:
        await usecase.request_account_deletion(user)
        event_type = "AccountDeletionRequested"
    events = await session.execute(select(Event).where(
        Event.aggregate_id == user.id, Event.event_type == event_type,
    ).order_by(Event.created_at.desc(), Event.id.desc()))
    # A second request can share the same transaction timestamp; resolve the unused token by its hash.
    token_model = EmailChangeToken if kind == "email" else AccountDeletionToken
    token_row = (await session.execute(select(token_model).where(token_model.used_at.is_(None)))).scalar_one()
    return next(event.payload["token"] for event in events.scalars()
                if TokenService().hash_opaque(event.payload["token"]) == token_row.token_hash)


async def _confirm(session, kind, raw):
    usecase = UserUseCase(session)
    if kind == "email":
        await usecase.confirm_email_change(raw)
    else:
        await usecase.confirm_account_deletion(raw)


@pytest.mark.parametrize("kind", ["email", "deletion"])
@pytest.mark.parametrize("state", ["invalid", "expired", "used", "replaced"])
async def test_confirmation_links_reject_invalid_expired_used_and_replaced(session, kind, state):
    user = await _buyer(session)
    raw = await _request_token(session, user, kind)
    model = EmailChangeToken if kind == "email" else AccountDeletionToken

    if state == "invalid":
        raw = "invalid-token"
    elif state == "expired":
        record = (await session.execute(select(model).where(model.user_id == user.id))).scalar_one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif state == "used":
        await _confirm(session, kind, raw)
    else:
        await _request_token(session, user, kind)

    with pytest.raises(GoneError):
        await _confirm(session, kind, raw)


async def test_deletion_preserves_identity_and_revokes_all_sessions(session):
    user = await _buyer(session)
    identity = (user.id, user.name, user.cpf, user.email)
    stamp = user.security_stamp
    await AuthUseCase(session).login(LoginRequest(email=user.email, password="senha-anterior-123"))
    raw = await _request_token(session, user, "deletion")
    await _confirm(session, "deletion", raw)

    stored = (await session.execute(select(User).where(User.id == identity[0]))).scalar_one()
    assert (stored.id, stored.name, stored.cpf, stored.email) == identity
    assert stored.is_active is False
    assert stored.security_stamp != stamp
    sessions = (await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    assert len(sessions) == 2
    assert all(not token.is_active for token in sessions)
    with pytest.raises(AuthError):
        await AuthUseCase(session).login(LoginRequest(email=identity[3], password="senha-anterior-123"))


async def test_password_change_revokes_other_sessions_and_requires_new_password(session):
    user = await _buyer(session)
    stamp = user.security_stamp
    auth = AuthUseCase(session)
    await auth.login(LoginRequest(email=user.email, password="senha-anterior-123"))
    await auth.change_password(user, ChangePasswordRequest(
        current_password="senha-anterior-123", new_password="senha-nova-123",
    ))

    assert user.security_stamp != stamp
    sessions = (await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    assert all(not token.is_active for token in sessions)
    with pytest.raises(AuthError):
        await auth.login(LoginRequest(email=user.email, password="senha-anterior-123"))
    access, refresh = await auth.login(LoginRequest(email=user.email, password="senha-nova-123"))
    assert access.access_token and refresh


async def test_logout_revokes_all_sessions(session):
    user = await _buyer(session)
    stamp = user.security_stamp
    await AuthUseCase(session).login(LoginRequest(email=user.email, password="senha-anterior-123"))
    await AuthUseCase(session).logout(user)

    assert user.security_stamp != stamp
    sessions = (await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    assert all(not token.is_active for token in sessions)


async def test_long_new_password_returns_422_and_preserves_current_credentials(session):
    user = await _buyer(session)
    access, _ = await AuthUseCase(session).login(LoginRequest(email=user.email, password="senha-anterior-123"))

    async def test_db():
        yield session

    app.dependency_overrides[get_db] = test_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            response = await client.post("/api/identity/authentication/password/change", json={
                "current_password": "senha-anterior-123", "new_password": "é" * 37,
            }, headers={"Authorization": f"Bearer {access.access_token}"})
        assert response.status_code == 422
        assert "senha é muito longa" in response.json()["detail"]
        token, _ = await AuthUseCase(session).login(LoginRequest(email=user.email, password="senha-anterior-123"))
        assert token.access_token
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("kind,method,path", [
    ("email", "PATCH", "/api/identity/users/email/change"),
    ("deletion", "DELETE", "/api/identity/users/deletion"),
])
async def test_public_confirmation_clears_cookie_and_rejects_reuse(session, kind, method, path):
    user = await _buyer(session)
    raw = await _request_token(session, user, kind)

    async def test_db():
        yield session

    app.dependency_overrides[get_db] = test_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            response = await client.request(method, path, params={"token": raw})
            assert response.status_code == 204
            cookie = response.headers["set-cookie"].lower()
            assert "max-age=0" in cookie
            assert "path=/api/identity/authentication" in cookie
            repeated = await client.request(method, path, params={"token": raw})
            assert repeated.status_code == 410
    finally:
        app.dependency_overrides.pop(get_db, None)

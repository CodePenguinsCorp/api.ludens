import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import ConflictError, GoneError
from app.modules.identity.application.schemas.request import RegisterRequest, RequestEmailChangeRequest
from app.modules.identity.application.usecases.user_usecase import UserUseCase
from app.modules.identity.domain.aggregates import User
from app.modules.identity.domain.entities.account_deletion_token import AccountDeletionToken
from app.modules.identity.domain.entities.email_change_token import EmailChangeToken
from app.modules.identity.domain.entities.refresh_token import RefreshToken
from app.modules.identity.domain.value_objects import CPF, Email
from app.modules.identity.infrastructure.repositories import UserRepository
from app.outbox.models import Event

pytestmark = pytest.mark.integration


async def _event_token(session, user_id, kind):
    event_type = "EmailChangeRequested" if kind == "email" else "AccountDeletionRequested"
    event = (await session.execute(select(Event).where(
        Event.aggregate_id == user_id, Event.event_type == event_type,
    ))).scalar_one()
    return event.payload["token"]


async def _concurrent_confirmations(engine, kind, tokens):
    ready = asyncio.Queue()
    start = asyncio.Event()

    async def confirm(raw):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                async with session.begin():
                    await session.execute(select(1))
                    ready.put_nowait(True)
                    await start.wait()
                    usecase = UserUseCase(session)
                    if kind == "email":
                        await usecase.confirm_email_change(raw)
                    else:
                        await usecase.confirm_account_deletion(raw)
                return "confirmed"
            except (GoneError, ConflictError) as error:
                return type(error).__name__

    tasks = [asyncio.create_task(confirm(token)) for token in tokens]
    for _ in tasks:
        await ready.get()
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)


async def _cleanup(engine, user_ids):
    async with AsyncSession(engine) as session, session.begin():
        for model in (RefreshToken, EmailChangeToken, AccountDeletionToken):
            await session.execute(delete(model).where(model.user_id.in_(user_ids)))
        await session.execute(delete(Event).where(Event.aggregate_id.in_(user_ids)))
        await session.execute(delete(User).where(User.id.in_(user_ids)))


@pytest.mark.parametrize("kind", ["email", "deletion"])
async def test_same_confirmation_token_can_only_commit_once_under_concurrency(engine, kind):
    user_ids = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            email = f"concurrency-{uuid4().hex}@example.com"
            usecase = UserUseCase(session)
            await usecase.register(RegisterRequest(
                name="Concurrency Buyer", cpf="52998224725", email=email, password="test-password-123",
            ))
            user = await UserRepository(session).find_by("email", email)
            user_ids.append(user.id)
            if kind == "email":
                await usecase.request_email_change(user, RequestEmailChangeRequest(new_email=f"new-{email}"))
            else:
                await usecase.request_account_deletion(user)
            raw = await _event_token(session, user.id, kind)

        results = await _concurrent_confirmations(engine, kind, [raw, raw])
        assert sorted(results) == ["GoneError", "confirmed"]
    finally:
        await _cleanup(engine, user_ids)


async def test_two_administrators_cannot_concurrently_delete_the_last_active_admin(engine):
    user_ids = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            users = []
            for cpf in ("52998224725", "11144477735"):
                user = User.register("Admin", CPF(cpf), Email(f"admin-{uuid4().hex}@example.com"), "hashed", is_admin=True)
                await UserRepository(session).save(user)
                users.append(user)
                user_ids.append(user.id)
            tokens = []
            for user in users:
                await UserUseCase(session).request_account_deletion(user)
                tokens.append(await _event_token(session, user.id, "deletion"))

        results = await _concurrent_confirmations(engine, "deletion", tokens)
        assert sorted(results) == ["ConflictError", "confirmed"]
        async with AsyncSession(engine) as session:
            remaining = (await session.execute(select(User).where(
                User.id.in_(user_ids), User.is_active.is_(True),
            ))).scalars().all()
            assert len(remaining) == 1
    finally:
        await _cleanup(engine, user_ids)


@pytest.mark.parametrize("kind", ["email", "deletion"])
async def test_concurrent_requests_leave_only_one_usable_confirmation_link(engine, kind):
    user_ids = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            email = f"request-{uuid4().hex}@example.com"
            await UserUseCase(session).register(RegisterRequest(
                name="Buyer", cpf="52998224725", email=email, password="test-password-123",
            ))
            user = await UserRepository(session).find_by("email", email)
            user_ids.append(user.id)

        ready = asyncio.Queue()
        start = asyncio.Event()

        async def request_link():
            async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
                buyer = await UserRepository(session).find_by("id", user_ids[0])
                ready.put_nowait(True)
                await start.wait()
                if kind == "email":
                    await UserUseCase(session).request_email_change(buyer, RequestEmailChangeRequest(new_email=f"new-{email}"))
                else:
                    await UserUseCase(session).request_account_deletion(buyer)

        tasks = [asyncio.create_task(request_link()) for _ in range(2)]
        for _ in tasks:
            await ready.get()
        start.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        model = EmailChangeToken if kind == "email" else AccountDeletionToken
        async with AsyncSession(engine) as session:
            usable = (await session.execute(select(model).where(
                model.user_id == user_ids[0], model.used_at.is_(None),
            ))).scalars().all()
            assert len(usable) == 1
    finally:
        await _cleanup(engine, user_ids)


async def test_two_buyers_cannot_confirm_the_same_email_under_concurrency(engine, monkeypatch):
    user_ids = []
    target = f"shared-{uuid4().hex}@example.com"
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            tokens = []
            for cpf in ("52998224725", "11144477735"):
                email = f"buyer-{uuid4().hex}@example.com"
                usecase = UserUseCase(session)
                await usecase.register(RegisterRequest(name="Buyer", cpf=cpf, email=email, password="test-password-123"))
                user = await UserRepository(session).find_by("email", email)
                user_ids.append(user.id)
                await usecase.request_email_change(user, RequestEmailChangeRequest(new_email=target))
                tokens.append(await _event_token(session, user.id, "email"))

        original_check = UserRepository.exists_by
        both_checked = asyncio.Event()
        arrivals = 0

        async def simultaneous_email_check(repository, field, value):
            nonlocal arrivals
            exists = await original_check(repository, field, value)
            if field == "email" and value == target:
                arrivals += 1
                if arrivals == 2:
                    both_checked.set()
                await both_checked.wait()
            return exists

        monkeypatch.setattr(UserRepository, "exists_by", simultaneous_email_check)
        results = await _concurrent_confirmations(engine, "email", tokens)
        assert sorted(results) == ["ConflictError", "confirmed"]
        async with AsyncSession(engine) as session:
            matching = (await session.execute(select(User).where(User.email == target))).scalars().all()
            assert len(matching) == 1
    finally:
        await _cleanup(engine, user_ids)

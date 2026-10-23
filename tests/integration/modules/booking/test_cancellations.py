from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.core.domain.errors import ConflictError, NotFoundError
from app.modules.booking.application.cancellation_usecase import CancellationUseCase
from app.modules.booking.application.purchase_usecase import PurchaseUseCase
from app.modules.booking.application.reservation_usecase import ReservationUseCase
from app.modules.booking.application.schemas import ReservationRequest
from app.modules.booking.domain.purchase import PaymentOrder, Ticket
from app.modules.catalog.application.usecases.session_usecase import SessionUseCase
from app.modules.catalog.domain.aggregates import Genre, Session, Show
from app.modules.catalog.infrastructure.repositories import GenreRepository, SessionRepository, ShowRepository
from app.modules.identity.domain.aggregates import User
from app.modules.identity.domain.value_objects import CPF, Email
from app.modules.identity.infrastructure.repositories import UserRepository
from app.modules.identity.infrastructure.services import TokenService
from app.outbox.models import Event

pytestmark = pytest.mark.integration


async def seed(db, *, capacity=8, days=5):
    genre = Genre.create(name=f"Cancel {uuid4().hex[:12]}")
    await GenreRepository(db).save(genre)
    await db.flush()
    show = Show.create(title="Cancelamento de teste", synopsis="...", image_url="/test.jpg", genre_id=genre.id)
    show.publish()
    await ShowRepository(db).save(show)
    await db.flush()
    event = Session.create(show_id=show.id, starts_at=datetime.now(timezone.utc) + timedelta(days=days),
        venue="Teatro", capacity=capacity, full_price_cents=1000)
    await SessionRepository(db).save(event)
    users = []
    for index, cpf in enumerate(("52998224725", "11144477735")):
        user = User.register(f"Buyer {index}", CPF(cpf), Email(f"{uuid4().hex}@example.com"), "not-a-real-password")
        await UserRepository(db).save(user)
        users.append(user)
    admin = User.register("Admin", CPF("12345678909"), Email(f"{uuid4().hex}@example.com"), "x", is_admin=True)
    await UserRepository(db).save(admin)
    await db.flush()
    return event, users, admin


async def buy(db, event, user, *, full=1, half=0, pay=True):
    reservation = await ReservationUseCase(db).create(
        ReservationRequest(session_id=event.id, full_quantity=full, half_quantity=half, idempotency_key=uuid4()),
        user_id=user.id, cpf=user.cpf)
    purchases = PurchaseUseCase(db)
    order = await purchases.start(reservation.id, user)
    return await purchases.simulate(order.id, "approved", user) if pay else order


async def available(db, event):
    return (await SessionUseCase(db).get_session_detail(event.id, is_admin=False)).available_count


async def event_count(db, kind, aggregate_id=None):
    stmt = select(func.count()).select_from(Event).where(Event.event_type == kind)
    if aggregate_id is not None:
        stmt = stmt.where(Event.aggregate_id == str(aggregate_id))
    return await db.scalar(stmt)


async def test_buyer_cancels_paid_order_with_full_simulated_refund_and_releases_capacity(session):
    event, users, _ = await seed(session, capacity=4, days=5)
    paid = await buy(session, event, users[0], full=2, half=1)
    assert await available(session, event) == 1

    preview = await CancellationUseCase(session).preview(paid.id, users[0])
    assert preview.can_cancel and preview.refund_percent == 100 and preview.refund_cents == paid.amount_cents
    assert preview.ticket_count == 3 and preview.is_refund_simulated is True
    assert await available(session, event) == 1  # a preview never changes anything

    cancelled = await CancellationUseCase(session).cancel(paid.id, users[0], "Mudei de planos")
    assert cancelled.status == "cancelled" and cancelled.cancel_origin == "buyer"
    assert cancelled.cancel_reason == "Mudei de planos" and cancelled.cancelled_at is not None
    assert cancelled.refund_percent == 100 and cancelled.refund_cents == paid.amount_cents
    assert {t.status for t in cancelled.tickets} == {"cancelled"}
    assert await available(session, event) == 4
    assert (await ReservationUseCase(session).get(paid.reservation_id, users[0].id)).status == "cancelled"
    assert await event_count(session, "OrderCancelled", paid.id) == 1


async def test_repeating_the_cancellation_changes_nothing_and_sends_one_email(session):
    event, users, _ = await seed(session, capacity=3)
    paid = await buy(session, event, users[0], full=1)
    first = await CancellationUseCase(session).cancel(paid.id, users[0], "primeiro motivo")
    again = await CancellationUseCase(session).cancel(paid.id, users[0], "outro motivo")
    assert again.cancelled_at == first.cancelled_at and again.cancel_reason == "primeiro motivo"
    assert await available(session, event) == 3  # capacity released once, never twice
    assert await event_count(session, "OrderCancelled", paid.id) == 1
    assert await event_count(session, "ReservationCancelled", paid.reservation_id) == 1


async def test_half_refund_between_24h_and_48h_and_blocked_below_24h(session):
    event, users, _ = await seed(session)
    paid = await buy(session, event, users[0], full=1)
    order = await session.get(PaymentOrder, paid.id)
    order.session_starts_at = datetime.now(timezone.utc) + timedelta(hours=30)
    await session.flush()
    preview = await CancellationUseCase(session).preview(paid.id, users[0])
    assert preview.refund_percent == 50 and preview.refund_cents == paid.amount_cents // 2

    order.session_starts_at = datetime.now(timezone.utc) + timedelta(hours=5)
    await session.flush()
    blocked = await CancellationUseCase(session).preview(paid.id, users[0])
    assert blocked.can_cancel is False and blocked.refund_percent is None
    with pytest.raises(ConflictError):
        await CancellationUseCase(session).cancel(paid.id, users[0], None)
    assert (await PurchaseUseCase(session).get(paid.id, users[0])).status == "paid"


async def test_pending_order_cancel_releases_hold_without_refund(session):
    event, users, _ = await seed(session, capacity=2)
    pending = await buy(session, event, users[0], full=2, pay=False)
    assert await available(session, event) == 0
    preview = await CancellationUseCase(session).preview(pending.id, users[0])
    assert preview.can_cancel and preview.refund_percent is None
    cancelled = await CancellationUseCase(session).cancel(pending.id, users[0], None)
    assert cancelled.status == "cancelled" and cancelled.refund_percent is None
    assert cancelled.cancel_reason == "Cancelado pelo comprador."
    assert await available(session, event) == 2
    assert await event_count(session, "OrderCancelled") == 0  # nothing was bought, nobody to notify


async def test_orders_are_private_and_only_paid_or_pending_can_be_cancelled(session):
    event, users, _ = await seed(session)
    paid = await buy(session, event, users[0])
    with pytest.raises(NotFoundError):
        await CancellationUseCase(session).cancel(paid.id, users[1], None)
    with pytest.raises(NotFoundError):
        await CancellationUseCase(session).preview(paid.id, users[1])
    declined = await PurchaseUseCase(session).simulate(
        (await buy(session, event, users[1], pay=False)).id, "declined", users[1])
    with pytest.raises(ConflictError):
        await CancellationUseCase(session).cancel(declined.id, users[1], None)


async def test_session_cancellation_cancels_every_order_ticket_and_notifies_buyers(session):
    event, users, admin = await seed(session, capacity=6, days=1)
    first = await buy(session, event, users[0], full=2)
    second = await buy(session, event, users[1], full=1, half=1)
    pending = await buy(session, event, users[1], full=1, pay=False)

    await SessionUseCase(session).cancel_session(event.id, admin.id)

    for paid, buyer in ((first, users[0]), (second, users[1])):
        order = await PurchaseUseCase(session).get(paid.id, buyer)
        assert order.status == "cancelled" and order.cancel_origin == "session"
        assert order.refund_percent == 100 and order.refund_cents == order.amount_cents  # even under 24h
        assert {t.status for t in order.tickets} == {"cancelled"}
        assert order.cancel_reason == "Sessão cancelada pelo teatro."
    stored = await session.get(PaymentOrder, first.id)
    assert stored.cancelled_by == admin.id and stored.cancelled_at is not None
    assert (await PurchaseUseCase(session).get(pending.id, users[1])).status == "cancelled"
    assert await event_count(session, "OrderCancelled") == 2
    assert await session.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == "valid")) == 0

    with pytest.raises(ConflictError):  # repeating the session cancellation is refused and changes nothing
        await SessionUseCase(session).cancel_session(event.id, admin.id)
    assert await event_count(session, "OrderCancelled") == 2


async def test_check_in_accepts_valid_ticket_once_and_rejects_cancelled_or_unknown(session):
    event, users, admin = await seed(session)
    paid = await buy(session, event, users[0], full=2)
    first, second = (t.code for t in paid.tickets)
    use_case = CancellationUseCase(session)

    result = await use_case.check_in(first)
    assert result.status == "used" and result.show_title == "Cancelamento de teste"
    with pytest.raises(ConflictError):
        await use_case.check_in(first)
    with pytest.raises(NotFoundError):
        await use_case.check_in("DEMO-INEXISTENTE")

    with pytest.raises(ConflictError):  # an order with a used ticket can no longer be cancelled by the buyer
        await use_case.cancel(paid.id, users[0], None)

    other = await buy(session, event, users[1], full=1)
    await use_case.cancel(other.id, users[1], None)
    with pytest.raises(ConflictError):
        await use_case.check_in(other.tickets[0].code)
    assert second  # untouched ticket stays valid


async def test_http_preview_cancel_history_and_admin_listing(session):
    from app.dependencies import get_db
    from app.main import app

    event, users, admin = await seed(session)
    paid = await buy(session, event, users[0], full=1, half=1)
    buyer_token, _ = TokenService().issue_access(users[0])
    admin_token, _ = TokenService().issue_access(admin)

    async def db_override():
        yield session

    app.dependency_overrides[get_db] = db_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            buyer = {"Authorization": f"Bearer {buyer_token}"}
            staff = {"Authorization": f"Bearer {admin_token}"}
            preview = await client.get(f"/api/booking/orders/{paid.id}/cancellation-preview", headers=buyer)
            assert preview.status_code == 200 and preview.json()["can_cancel"] is True
            assert "simulado" in preview.json()["message"].lower()

            done = await client.post(f"/api/booking/orders/{paid.id}/cancel", json={"reason": "Teste"}, headers=buyer)
            assert done.status_code == 200 and done.json()["status"] == "cancelled"
            repeated = await client.post(f"/api/booking/orders/{paid.id}/cancel", headers=buyer)
            assert repeated.status_code == 200 and repeated.json()["cancelled_at"] == done.json()["cancelled_at"]
            history = await client.get("/api/booking/orders", headers=buyer)
            assert history.json()[0]["cancel_reason"] == "Teste"

            assert (await client.get("/api/booking/admin/orders", headers=buyer)).status_code == 403
            listing = await client.get("/api/booking/admin/orders?status=cancelled", headers=staff)
            assert listing.status_code == 200 and listing.json()["total"] == 1
            assert listing.json()["items"][0]["buyer_email"] == users[0].email

            code = paid.tickets[0].code
            assert (await client.post("/api/booking/tickets/check-in", json={"code": code}, headers=buyer)).status_code == 403
            assert (await client.post("/api/booking/tickets/check-in", json={"code": code}, headers=staff)).status_code == 409
    finally:
        app.dependency_overrides.clear()

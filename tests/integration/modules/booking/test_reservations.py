import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.domain.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.core.shared import PaginationParams
from app.modules.booking.application.reservation_usecase import ReservationUseCase
from app.modules.booking.application.schemas import ReservationRequest
from app.modules.booking.domain.reservation import Reservation, ReservationStatus
from app.modules.booking.infrastructure.reservation_repository import ReservationRepository
from app.modules.catalog.application.schemas.request import SessionRequest
from app.modules.catalog.application.usecases.session_usecase import SessionUseCase
from app.modules.catalog.application.usecases.show_usecase import ShowUseCase
from app.modules.catalog.domain.aggregates import Genre, Session, Show
from app.modules.catalog.infrastructure.repositories import GenreRepository, SessionRepository, ShowRepository
from app.modules.identity.domain.aggregates import User
from app.modules.identity.domain.value_objects import CPF, Email
from app.modules.identity.infrastructure.repositories import UserRepository
from app.modules.identity.infrastructure.services import TokenService
from app.outbox.models import Event

pytestmark = pytest.mark.integration


async def seed(db, *, capacity=8):
    genre = Genre.create(name=f"Booking {uuid4().hex[:12]}")
    await GenreRepository(db).save(genre)
    await db.flush()
    show = Show.create(title="Reserva de teste", synopsis="...", image_url="/test.jpg", genre_id=genre.id)
    show.publish()
    await ShowRepository(db).save(show)
    await db.flush()
    session = Session.create(show_id=show.id, starts_at=datetime.now(timezone.utc) + timedelta(days=2),
        venue="Teatro", capacity=capacity, full_price_cents=1001)
    await SessionRepository(db).save(session)
    users = []
    for index, cpf in enumerate(("52998224725", "11144477735", "12345678909")):
        user = User.register(f"Buyer {index}", CPF(cpf), Email(f"{uuid4().hex}@example.com"), "not-a-real-password")
        await UserRepository(db).save(user)
        users.append(user)
    await db.flush()
    return session, users, show, genre


def request(session_id, full=1, half=0, key=None):
    return ReservationRequest(session_id=session_id, full_quantity=full, half_quantity=half, idempotency_key=key or uuid4())


async def reserve(db, session_id, user, **kwargs):
    return await ReservationUseCase(db).create(request(session_id, **kwargs), user_id=user.id, cpf=user.cpf)


async def test_create_snapshot_inventory_and_recovery_in_new_usecase(session):
    event, users, show, _ = await seed(session)
    result = await reserve(session, event.id, users[0], full=2, half=1)
    assert result.status == "open"
    assert result.total_cents == 2502
    assert result.expires_at - result.created_at == timedelta(minutes=15)
    assert (await SessionUseCase(session).get_session_detail(event.id, is_admin=False)).available_count == 5
    detail = await ShowUseCase(session).get_show_detail(show.id, is_admin=False)
    assert detail.sessions[0].available_count == 5
    admin = await SessionUseCase(session).get_session_detail(event.id, is_admin=True)
    assert admin.reserved_open == 3
    recovered = await ReservationUseCase(session).get(result.id, users[0].id)
    assert recovered.id == result.id and recovered.total_cents == 2502
    assert await session.scalar(select(func.count()).select_from(Event).where(Event.event_type == "ReservationCreated")) == 1


async def test_idempotency_replay_and_different_payload(session):
    event, users, *_ = await seed(session)
    req = request(event.id, full=2)
    uc = ReservationUseCase(session)
    first = await uc.create(req, user_id=users[0].id, cpf=users[0].cpf)
    second = await uc.create(req, user_id=users[0].id, cpf=users[0].cpf)
    assert first.id == second.id
    with pytest.raises(ConflictError):
        await uc.create(req.model_copy(update={"full_quantity": 1}), user_id=users[0].id, cpf=users[0].cpf)


@pytest.mark.parametrize("full,half", [(0, 0), (4, 3)])
async def test_invalid_totals(session, full, half):
    event, users, *_ = await seed(session)
    with pytest.raises(DomainError):
        await reserve(session, event.id, users[0], full=full, half=half)


async def test_cpf_limit_includes_confirmed_and_multiple_open_reservations(session):
    event, users, *_ = await seed(session, capacity=20)
    first = await reserve(session, event.id, users[0], full=4)
    entity = await session.get(Reservation, first.id)
    entity.confirm(datetime.now(timezone.utc))
    await ReservationRepository(session).save(entity)
    await session.flush()
    await reserve(session, event.id, users[0], full=1, half=1)
    with pytest.raises(ConflictError, match="CPF"):
        await reserve(session, event.id, users[0])
    # Even another identity carrying the same CPF cannot bypass the limit.
    with pytest.raises(ConflictError, match="CPF"):
        await ReservationUseCase(session).create(request(event.id), user_id=users[1].id, cpf=users[0].cpf)
    detail = await SessionUseCase(session).get_session_detail(event.id, is_admin=True)
    assert detail.tickets_sold == 4 and detail.reserved_open == 2


@pytest.mark.parametrize("state", ["draft", "cancelled", "past", "deleted"])
async def test_closed_sessions_and_admin_cannot_reserve(session, state):
    event, users, show, _ = await seed(session)
    if state == "draft":
        show.unpublish()
    elif state == "cancelled":
        event.cancel()
    elif state == "deleted":
        event.deactivate()
    else:
        event.starts_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.flush()
    with pytest.raises((ConflictError, NotFoundError)):
        await reserve(session, event.id, users[0])
    with pytest.raises(ForbiddenError):
        await ReservationUseCase(session).create(request(event.id), user_id=users[0].id, cpf=users[0].cpf, is_admin=True)


async def test_cancel_ownership_idempotency_and_inventory(session):
    event, users, *_ = await seed(session, capacity=2)
    reservation = await reserve(session, event.id, users[0], full=2)
    uc = ReservationUseCase(session)
    with pytest.raises(NotFoundError):
        await uc.get(reservation.id, users[1].id, cancel=True)
    assert (await uc.get(reservation.id, users[0].id)).status == "open"
    assert (await uc.get(reservation.id, users[0].id, cancel=True)).status == "cancelled"
    assert (await uc.get(reservation.id, users[0].id, cancel=True)).status == "cancelled"
    assert (await SessionUseCase(session).get_session_detail(event.id, is_admin=False)).available_count == 2
    assert await session.scalar(select(func.count()).select_from(Event).where(Event.event_type == "ReservationCancelled")) == 1


async def test_expired_reservation_frees_capacity_before_worker_and_expires_once(session):
    event, users, *_ = await seed(session, capacity=2)
    result = await reserve(session, event.id, users[0], full=2)
    entity = await session.get(Reservation, result.id)
    entity.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.flush()
    assert (await SessionUseCase(session).get_session_detail(event.id, is_admin=False)).available_count == 2
    await reserve(session, event.id, users[1], full=2)
    repo = ReservationRepository(session)
    assert await repo.expire_due(datetime.now(timezone.utc)) == 1
    assert await repo.expire_due(datetime.now(timezone.utc)) == 0
    assert (await ReservationUseCase(session).get(result.id, users[0].id)).status == "expired"
    assert await session.scalar(select(func.count()).select_from(Event).where(Event.event_type == "ReservationExpired")) == 1


async def test_session_admin_cannot_reduce_capacity_and_cancellation_cancels_holds(session):
    event, users, *_ = await seed(session)
    result = await reserve(session, event.id, users[0], full=3)
    with pytest.raises(ConflictError):
        await SessionUseCase(session).update_session(event.id, SessionRequest(show_id=event.show_id,
            starts_at=event.starts_at, venue=event.venue, capacity=2, full_price=10.01))
    await SessionUseCase(session).cancel_session(event.id)
    assert (await ReservationUseCase(session).get(result.id, users[0].id)).status == "cancelled"


async def test_http_requires_auth_hides_other_users_and_lists_only_owner(session):
    from app.dependencies import get_db
    from app.main import app

    event, users, *_ = await seed(session)
    reservation = await reserve(session, event.id, users[0])

    async def db_override():
        yield session

    app.dependency_overrides[get_db] = db_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = "/api/booking/reservations"
            assert (await client.get(path)).status_code == 401
            assert (await client.get(f"{path}/{reservation.id}/rebooking-options")).status_code == 401
            assert (await client.post(path, json=request(event.id).model_dump(mode="json"))).status_code == 401
            other_token, _ = TokenService().issue_access(users[1])
            client.headers["Authorization"] = f"Bearer {other_token}"
            assert (await client.get(f"{path}/{reservation.id}")).status_code == 404
            assert (await client.get(f"{path}/{reservation.id}/rebooking-options")).status_code == 404
            assert (await client.post(f"{path}/{reservation.id}/cancel")).status_code == 404
            assert (await client.get(path)).json()["items"] == []
            owner_token, _ = TokenService().issue_access(users[0])
            client.headers["Authorization"] = f"Bearer {owner_token}"
            response = await client.get(path)
            assert response.status_code == 200 and response.json()["total"] == 1
            assert response.headers["cache-control"] == "no-store"
            options = await client.get(f"{path}/{reservation.id}/rebooking-options")
            assert options.status_code == 200 and options.json()["sessions"] == []
            assert options.headers["cache-control"] == "no-store"
            assert (await client.get(f"{path}?page=0")).status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def committed_data(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as db:
        data = await seed(db, capacity=3)
    yield factory, data
    event, users, show, genre = data
    async with factory.begin() as db:
        reservation_ids = list(await db.scalars(select(Reservation.id).where(Reservation.session_id == event.id)))
        await db.execute(delete(Event).where(Event.aggregate_id.in_([event.id, show.id, genre.id, *[u.id for u in users], *reservation_ids])))
        await db.execute(delete(Reservation).where(Reservation.session_id == event.id))
        await db.execute(delete(Session).where(Session.id == event.id))
        await db.execute(delete(Show).where(Show.id == show.id))
        await db.execute(delete(Genre).where(Genre.id == genre.id))
        await db.execute(delete(User).where(User.id.in_([u.id for u in users])))


async def race(factory, calls):
    start = asyncio.Event()

    async def attempt(req, user):
        async with factory() as db:
            try:
                async with db.begin():
                    await start.wait()
                    result = await ReservationUseCase(db).create(req, user_id=user.id, cpf=user.cpf)
                    await asyncio.sleep(0.05)  # Keep the row locked while the other connections compete.
                return result
            except ConflictError as exc:
                return exc

    tasks = [asyncio.create_task(attempt(req, user)) for req, user in calls]
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)


async def test_concurrent_last_tickets_never_oversell(committed_data):
    factory, (event, users, *_) = committed_data
    results = await race(factory, [(request(event.id, full=2), user) for user in users])
    assert sum(not isinstance(result, ConflictError) for result in results) == 1
    async with factory() as db:
        counts = await ReservationRepository(db).counts([event.id], datetime.now(timezone.utc))
        assert counts[event.id] == (0, 2)


async def test_concurrent_duplicate_requests_create_one_reservation_and_recover_after_restart(committed_data):
    factory, (event, users, *_) = committed_data
    req = request(event.id, full=2)
    results = await race(factory, [(req, users[0])] * 3)
    assert len({r.id for r in results}) == 1
    async with factory.begin() as db:
        recovered = await ReservationUseCase(db).get(results[0].id, users[0].id)
        assert recovered.status == "open" and recovered.full_quantity == 2
        entity = await db.get(Reservation, recovered.id)
        entity.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    # Simulate application restart: new session, new worker, only persisted state.
    async with factory.begin() as db:
        assert await ReservationRepository(db).expire_due(datetime.now(timezone.utc)) == 1
    async with factory.begin() as db:
        assert (await ReservationUseCase(db).get(results[0].id, users[0].id)).status == "expired"


async def test_concurrent_cpf_limit_serializes_even_with_spare_capacity(committed_data):
    factory, (event, users, *_) = committed_data
    async with factory.begin() as db:
        entity = await db.get(Session, event.id)
        entity.capacity = 30
    results = await race(factory, [(request(event.id, full=4), users[0]) for _ in range(2)])
    assert sum(not isinstance(result, ConflictError) for result in results) == 1


async def test_rebooking_sorts_other_future_sessions_and_excludes_unavailable_and_other_shows(session):
    original, users, show, genre = await seed(session)
    reservation = await reserve(session, original.id, users[0])
    await ReservationUseCase(session).get(reservation.id, users[0].id, cancel=True)
    original.starts_at = datetime.now(timezone.utc) - timedelta(days=1)
    now = datetime.now(timezone.utc)

    async def add(hours, *, show_id=show.id, capacity=2):
        event = Session.create(show_id=show_id, starts_at=now + timedelta(hours=hours),
            venue=f"Teatro {hours}", capacity=capacity, full_price_cents=1000)
        await SessionRepository(session).save(event)
        await session.flush()
        return event

    later = await add(12)
    nearest = await add(6)
    held = await add(1)
    await reserve(session, held.id, users[1], full=2)
    sold = await add(2)
    purchase = await reserve(session, sold.id, users[1], full=2)
    entity = await session.get(Reservation, purchase.id)
    entity.confirm(now)
    await ReservationRepository(session).save(entity)
    cancelled = await add(3)
    cancelled.cancel()
    inactive = await add(4)
    inactive.deactivate()
    await add(-1)
    other = Show.create(title="Outro espetáculo", synopsis="...", image_url="/test.jpg", genre_id=genre.id)
    other.publish()
    await ShowRepository(session).save(other)
    await session.flush()
    await add(5, show_id=other.id)
    await session.flush()

    result = await ReservationUseCase(session).rebooking_options(reservation.id, users[0].id)
    assert [option.id for option in result.sessions] == [nearest.id, later.id]
    assert all(option.available_count == 2 and option.starts_at > result.server_now for option in result.sessions)
    assert result.sessions[0].venue == nearest.venue


async def test_rebooking_releases_expired_holds_and_resolves_deactivated_original_session(session):
    original, users, show, _ = await seed(session)
    previous = await reserve(session, original.id, users[0])
    original.deactivate()
    next_session = Session.create(show_id=show.id, starts_at=original.starts_at + timedelta(days=1),
        venue="Nova sala", capacity=2, full_price_cents=1000)
    await SessionRepository(session).save(next_session)
    await session.flush()
    held = await reserve(session, next_session.id, users[1], full=2)
    entity = await session.get(Reservation, held.id)
    entity.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.flush()
    result = await ReservationUseCase(session).rebooking_options(previous.id, users[0].id)
    assert [option.id for option in result.sessions] == [next_session.id]
    assert result.sessions[0].available_count == 2


@pytest.mark.parametrize("state", ["draft", "deleted"])
async def test_rebooking_has_no_options_for_unpublished_or_deleted_show(session, state):
    original, users, show, _ = await seed(session)
    previous = await reserve(session, original.id, users[0])
    other = Session.create(show_id=show.id, starts_at=original.starts_at + timedelta(days=1),
        venue="Outra sala", capacity=10, full_price_cents=1000)
    await SessionRepository(session).save(other)
    if state == "draft":
        show.unpublish()
    else:
        show.deactivate()
    await session.flush()
    assert (await ReservationUseCase(session).rebooking_options(previous.id, users[0].id)).sessions == []

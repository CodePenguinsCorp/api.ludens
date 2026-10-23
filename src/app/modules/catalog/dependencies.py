"""Public catalog snapshot and row lock for booking (RN05)."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.domain.errors import NotFoundError
from app.modules.catalog.domain.enumerations import SessionStatus, ShowStatus
from app.modules.catalog.infrastructure.repositories import SessionRepository
from app.modules.catalog.domain.aggregates import Session, Show
from app.modules.booking.dependencies import reservation_counts


@dataclass(frozen=True)
class BookableSession:
    id: UUID
    capacity: int
    full_price_cents: int
    half_price_cents: int
    show_title: str
    starts_at: datetime
    venue: str
    on_sale: bool


async def lock_booking_session(db: AsyncSession, session_id: UUID) -> BookableSession:
    session = await SessionRepository(db).find_by_id_for_update(session_id)
    if session is None:
        raise NotFoundError("Sessão não encontrada.")
    show = await db.scalar(select(Show).where(Show.id == session.show_id, Show.is_active.is_(True))
        .with_for_update(read=True).execution_options(populate_existing=True))
    return BookableSession(
        id=session.id, capacity=session.capacity, full_price_cents=session.full_price_cents,
        half_price_cents=session.half_price_cents, show_title=show.title if show else "",
        starts_at=session.starts_at, venue=session.venue,
        on_sale=bool(show and show.is_published and session.status is SessionStatus.ON_SALE),
    )


@dataclass(frozen=True)
class RebookingSession:
    id: UUID
    starts_at: datetime
    venue: str
    available_count: int


async def rebooking_sessions(db: AsyncSession, original_session_id: UUID, now: datetime) -> list[RebookingSession]:
    # Historical reservations keep their session FK even after it is deactivated.
    original = await db.get(Session, original_session_id)
    if original is None:
        return []
    sessions = list(await db.scalars(
        select(Session).join(Show, Show.id == Session.show_id).where(
            Session.show_id == original.show_id,
            Session.id != original_session_id,
            Session.is_active.is_(True),
            Session.status == SessionStatus.ON_SALE,
            Session.starts_at > now,
            Show.is_active.is_(True),
            Show.status == ShowStatus.PUBLISHED,
        ).order_by(Session.starts_at, Session.id)
    ))
    counts = await reservation_counts(db, [s.id for s in sessions], now)
    available = []
    for session in sessions:
        sold, held = counts.get(session.id, (0, 0))
        remaining = max(0, session.capacity - sold - held)
        if remaining > 0:
            available.append(RebookingSession(id=session.id, starts_at=session.starts_at,
                venue=session.venue, available_count=remaining))
    return available

"""Public inventory contract consumed by catalog, in the caller's transaction."""
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.booking.infrastructure.reservation_repository import ReservationRepository


async def reservation_counts(db: AsyncSession, session_ids: list[UUID], now: datetime) -> dict[UUID, tuple[int, int]]:
    return await ReservationRepository(db).counts(session_ids, now)


async def cancel_session_reservations(db: AsyncSession, session_id: UUID, now: datetime) -> None:
    await ReservationRepository(db).cancel_for_session(session_id, now)

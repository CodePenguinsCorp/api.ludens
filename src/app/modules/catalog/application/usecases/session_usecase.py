from __future__ import annotations

from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.errors import ConflictError, DomainError, NotFoundError
from app.modules.booking.dependencies import reservation_counts, cancel_session_reservations

from app.modules.catalog.domain.enumerations import SessionStatus
from app.modules.catalog.domain.aggregates import SeatCounts, Session
from app.modules.catalog.application.schemas.request import SessionRequest
from app.modules.catalog.application.schemas.response import (
    AdminSessionResponse,
    SessionDetailResponse,
    SessionShowRef,
    TicketTypeResponse,
)
from app.modules.catalog.application.mappers import session_response
from app.modules.catalog.application.utils import cents_from_reais, reais_from_cents
from app.modules.catalog.infrastructure.repositories import SessionRepository, ShowRepository

class SessionUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._show_repository = ShowRepository(session)
        self._session_repository = SessionRepository(session)

    async def create_session(self, req: SessionRequest) -> AdminSessionResponse:
        show = await self._show_repository.find_by("id", req.show_id)
        if show is None:
            raise NotFoundError("Espetáculo não encontrado.")

        now = datetime.now(timezone.utc)
        if req.starts_at <= now:
            raise DomainError("A data da sessão deve ser futura.")
        if req.capacity <= 0:
            raise DomainError("A capacidade deve ser maior que zero.")

        session = Session.create(
            show_id=show.id,
            starts_at=req.starts_at,
            venue=req.venue,
            capacity=req.capacity,
            full_price_cents=cents_from_reais(req.full_price),
        )

        await self._session_repository.save(session)
        return session_response(session, SeatCounts(0, 0), now)

    async def update_session(self, session_id: UUID, req: SessionRequest) -> AdminSessionResponse:
        session = await self._lock(session_id)
        if session.status is SessionStatus.CANCELLED:
            raise ConflictError("Não é possível editar uma sessão cancelada.")

        now = datetime.now(timezone.utc)
        counts = SeatCounts(*(await reservation_counts(self._db, [session.id], now)).get(session.id, (0, 0)))

        if req.starts_at <= now:
            raise DomainError("A data da sessão deve ser futura.")

        committed = counts.tickets_sold + counts.reserved_open
        if req.capacity < committed:
            raise ConflictError("Já há ingressos comprometidos nesta sessão.")

        session.update(
            starts_at=req.starts_at,
            venue=req.venue,
            capacity=req.capacity,
            full_price_cents=cents_from_reais(req.full_price),
        )

        await self._session_repository.save(session)
        return session_response(session, counts, now)

    async def cancel_session(self, session_id: UUID, admin_id: UUID | None = None) -> None:
        session = await self._lock(session_id)
        if session.status is SessionStatus.CANCELLED:
            raise ConflictError("A sessão já está cancelada.")

        session.cancel()
        await cancel_session_reservations(self._db, session.id, datetime.now(timezone.utc), admin_id)
        await self._session_repository.save(session)

    async def delete_session(self, session_id: UUID) -> None:
        session = await self._lock(session_id)
        counts = SeatCounts(*(await reservation_counts(self._db, [session.id], datetime.now(timezone.utc))).get(session.id, (0, 0)))

        if counts.tickets_sold > 0:
            raise ConflictError("Cancele a sessão em vez de excluir.")

        session.deactivate()
        await cancel_session_reservations(self._db, session.id, datetime.now(timezone.utc))
        await self._session_repository.save(session)

    async def get_session_detail(self, session_id: UUID, *, is_admin: bool) -> AdminSessionResponse | SessionDetailResponse:
        session = await self._session_repository.find_by("id", session_id)
        if session is None:
            raise NotFoundError("Sessão não encontrada.")

        show = await self._show_repository.find_by("id", session.show_id)
        if show is None:
            raise NotFoundError("Espetáculo não encontrado.")

        now = datetime.now(timezone.utc)
        counts = SeatCounts(*(await reservation_counts(self._db, [session.id], now)).get(session.id, (0, 0)))

        if is_admin:
            return session_response(session, counts, now)

        if not show.is_published:
            raise NotFoundError("Espetáculo não encontrado.")

        available = session.available_count(counts)

        return SessionDetailResponse(
            id=session.id,
            show=SessionShowRef(id=show.id, title=show.title),
            starts_at=session.starts_at,
            venue=session.venue,
            capacity=session.capacity,
            available_count=available,
            status=session.status_at(now, counts),
            ticket_types=[
                TicketTypeResponse(type="full", price=reais_from_cents(session.full_price_cents)),
                TicketTypeResponse(type="half", price=reais_from_cents(session.half_price_cents)),
            ],
        )

    async def _lock(self, session_id: UUID) -> Session:
        session = await self._session_repository.find_by_id_for_update(session_id)
        if session is None:
            raise NotFoundError("Sessão não encontrada.")

        return session

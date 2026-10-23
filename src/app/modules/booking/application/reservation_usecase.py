from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.domain.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.core.shared import Page, PaginationParams
from app.modules.booking.application.schemas import (
    RebookingOptionsResponse, RebookingSessionResponse, ReservationRequest, ReservationResponse,
)
from app.modules.booking.domain.reservation import Reservation, ReservationStatus
from app.modules.booking.domain.purchase import OrderStatus, PaymentOrder
from app.modules.catalog.domain.aggregates import Session
from app.modules.booking.infrastructure.reservation_repository import ReservationRepository
from app.modules.catalog.dependencies import lock_booking_session, rebooking_sessions


def reservation_response(reservation: Reservation, now: datetime) -> ReservationResponse:
    values = {name: getattr(reservation, name) for name in ReservationResponse.model_fields if name != "server_now"}
    # A read remains correct even if another worker currently holds the expiry lock.
    if reservation.status == "open" and reservation.expires_at <= now:
        values["status"] = "expired"
    return ReservationResponse(**values, server_now=now)


class ReservationUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repository = ReservationRepository(db)

    async def create(self, req: ReservationRequest, *, user_id: UUID, cpf: str, is_admin: bool = False) -> ReservationResponse:
        if is_admin:
            raise ForbiddenError("Administradores não podem reservar pela área do comprador.")
        if not 1 <= req.full_quantity + req.half_quantity <= settings.max_tickets_per_cpf:
            raise DomainError(f"Selecione de 1 a {settings.max_tickets_per_cpf} ingressos.")
        existing = await self._repository.find_request(user_id, req.idempotency_key)
        if existing is not None:
            return self._replay(existing, req)

        session = await lock_booking_session(self._db, req.session_id)
        # Re-read AFTER the session lock: a competing transaction may have committed.
        existing = await self._repository.find_request(user_id, req.idempotency_key)
        if existing is not None:
            return self._replay(existing, req)
        now = await self._db.scalar(select(func.clock_timestamp()))
        if not session.on_sale or session.starts_at <= now:
            raise ConflictError("Esta sessão não está mais à venda.")
        quantity = req.full_quantity + req.half_quantity
        owned = await self._repository.count_cpf(session.id, cpf, now)
        if owned + quantity > settings.max_tickets_per_cpf:
            raise ConflictError(f"Você já atingiu o limite de {settings.max_tickets_per_cpf} ingressos por sessão por CPF.")
        sold, held = (await self._repository.counts([session.id], now)).get(session.id, (0, 0))
        if sold + held + quantity > session.capacity:
            raise ConflictError("Não há ingressos suficientes para esta sessão.")

        reservation = Reservation.create(
            now=now, user_id=user_id, buyer_cpf=cpf, session_id=session.id,
            idempotency_key=req.idempotency_key, full_quantity=req.full_quantity, half_quantity=req.half_quantity,
            full_price_cents=session.full_price_cents, half_price_cents=session.half_price_cents,
            show_title=session.show_title, starts_at=session.starts_at, venue=session.venue,
            expires_at=now + timedelta(minutes=settings.reservation_ttl_minutes),
        )
        try:
            async with self._db.begin_nested():
                await self._repository.save(reservation)
                await self._db.flush()
        except IntegrityError:
            # The same key used against different sessions is protected by the DB too.
            existing = await self._repository.find_request(user_id, req.idempotency_key)
            if existing is None:
                raise
            return self._replay(existing, req)
        return reservation_response(reservation, now)

    def _replay(self, existing: Reservation, req: ReservationRequest) -> ReservationResponse:
        if (existing.session_id, existing.full_quantity, existing.half_quantity) != (req.session_id, req.full_quantity, req.half_quantity):
            raise ConflictError("Esta tentativa já foi utilizada com outra seleção de ingressos.")
        return reservation_response(existing, datetime.now(timezone.utc))

    async def list(self, user_id: UUID, pagination: PaginationParams) -> Page[ReservationResponse]:
        now = datetime.now(timezone.utc)
        await self._repository.expire_due(now, user_id=user_id)
        reservations, total = await self._repository.list_owned(user_id, page=pagination.page, size=pagination.size)
        return Page(items=[reservation_response(r, now) for r in reservations], total=total, page=pagination.page, size=pagination.size)

    async def get(self, reservation_id: UUID, user_id: UUID, *, cancel: bool = False) -> ReservationResponse:
        reservation = await self._repository.find_owned(reservation_id, user_id, lock=False)
        if reservation is None:
            raise NotFoundError("Reserva não encontrada.")
        await self._db.scalar(select(Session).where(Session.id == reservation.session_id).with_for_update())
        await self._db.scalar(select(PaymentOrder).where(
            PaymentOrder.reservation_id == reservation.id,
            PaymentOrder.status == OrderStatus.PENDING,
        ).with_for_update())
        reservation = await self._repository.find_owned(reservation_id, user_id, lock=True)
        if reservation is None:
            raise NotFoundError("Reserva não encontrada.")
        now = datetime.now(timezone.utc)
        if cancel:
            reservation.cancel(now)
        else:
            reservation.expire(now)
        await self._repository.save(reservation)
        if reservation.status in {ReservationStatus.CANCELLED, ReservationStatus.EXPIRED}:
            await self._repository.finish_pending_order(reservation, now)
        await self._db.flush()
        return reservation_response(reservation, now)

    async def rebooking_options(self, reservation_id: UUID, user_id: UUID) -> RebookingOptionsResponse:
        reservation = await self._repository.find_owned(reservation_id, user_id)
        if reservation is None:
            raise NotFoundError("Reserva não encontrada.")
        now = datetime.now(timezone.utc)
        sessions = await rebooking_sessions(self._db, reservation.session_id, now)
        return RebookingOptionsResponse(server_now=now, sessions=[
            RebookingSessionResponse(id=s.id, starts_at=s.starts_at, venue=s.venue, available_count=s.available_count)
            for s in sessions
        ])

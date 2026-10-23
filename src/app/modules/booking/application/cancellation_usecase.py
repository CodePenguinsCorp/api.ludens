from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.errors import ConflictError, DomainError, NotFoundError
from app.core.shared import Page, PaginationParams
from app.modules.booking.application.cancellation_schemas import (
    AdminOrderResponse, CancellationPreviewResponse, CheckInResponse,
)
from app.modules.booking.application.purchase_schemas import OrderResponse
from app.modules.booking.application.purchase_usecase import PurchaseUseCase
from app.modules.booking.domain.purchase import (
    CancelOrigin, OrderStatus, PaymentOrder, Ticket, TicketStatus, simulated_refund_percent,
)
from app.modules.booking.domain.reservation import Reservation, ReservationStatus
from app.modules.booking.infrastructure.reservation_repository import ReservationRepository
from app.modules.catalog.dependencies import lock_booking_session
from app.modules.identity.domain.aggregates import User
from app.outbox.models import Event

SIMULATION_NOTE = "Pagamento simulado: nenhum valor real foi cobrado nem será devolvido."
BUYER_REASON = "Cancelado pelo comprador."
SESSION_REASON = "Sessão cancelada pelo teatro."


def _refund_cents(order: PaymentOrder, percent: int) -> int:
    return order.amount_cents * percent // 100


async def cancel_paid_order(
    db: AsyncSession, order: PaymentOrder, reservation: Reservation | None, *, now: datetime,
    origin: CancelOrigin, by: UUID | None, reason: str, percent: int,
) -> None:
    """Cancels a paid order once: invalidates tickets, releases the confirmed reservation, queues the e-mail."""
    if order.status is not OrderStatus.PAID:
        return
    tickets = list(await db.scalars(select(Ticket).where(Ticket.order_id == order.id)
        .with_for_update().execution_options(populate_existing=True)))
    for ticket in tickets:
        ticket.status = TicketStatus.CANCELLED
    if reservation is not None and reservation.status is ReservationStatus.CONFIRMED:
        reservation.cancel(now, allow_confirmed=True)
        await ReservationRepository(db).save(reservation)
    order.status = OrderStatus.CANCELLED
    order.resolved_at = now
    order.cancelled_at = now
    order.cancelled_by = by
    order.cancel_origin = origin
    order.cancel_reason = reason
    order.refund_percent = percent
    order.refund_cents = _refund_cents(order, percent)
    buyer = await db.get(User, order.user_id)
    db.add(Event(
        aggregate_id=str(order.id),
        event_type="OrderCancelled",
        payload={
            "email": buyer.email if buyer else None,
            "order_id": str(order.id),
            "origin": origin.value,
            "reason": reason,
            "show_title": order.show_title,
            "session_starts_at": order.session_starts_at.isoformat(),
            "venue": order.venue,
            "amount_cents": order.amount_cents,
            "refund_percent": percent,
            "refund_cents": order.refund_cents,
            "ticket_count": len(tickets),
        },
    ))


async def cancel_paid_orders_for_session(db: AsyncSession, session_id: UUID, now: datetime, admin_id: UUID | None) -> None:
    """Session cancelled by the theatre: every paid order is cancelled with a 100% simulated refund."""
    orders = list(await db.scalars(select(PaymentOrder).where(
        PaymentOrder.session_id == session_id, PaymentOrder.status == OrderStatus.PAID,
    ).order_by(PaymentOrder.id).with_for_update().execution_options(populate_existing=True)))
    for order in orders:
        reservation = await db.scalar(select(Reservation).where(Reservation.id == order.reservation_id)
            .with_for_update().execution_options(populate_existing=True))
        await cancel_paid_order(db, order, reservation, now=now, origin=CancelOrigin.SESSION,
                                by=admin_id, reason=SESSION_REASON, percent=100)
    await db.flush()


class CancellationUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._purchases = PurchaseUseCase(db)

    async def preview(self, order_id: UUID, user: User) -> CancellationPreviewResponse:
        order = await self._purchases._owned_order(order_id, user.id)
        now = await self._purchases._now()
        tickets = await self._purchases._tickets(order.id)
        count = len(tickets)
        if order.status is OrderStatus.PENDING:
            return self._preview(order, count, True, None, None,
                "A reserva será cancelada e os ingressos voltam a ficar disponíveis. Nada foi cobrado.")
        if order.status is not OrderStatus.PAID:
            return self._preview(order, count, False, order.refund_percent, order.refund_cents,
                "Este pedido não pode mais ser cancelado.")
        if any(t.status is TicketStatus.USED for t in tickets):
            return self._preview(order, count, False, None, None, "Há ingressos já utilizados no check-in.")
        if order.session_starts_at <= now:
            return self._preview(order, count, False, None, None, "A sessão já começou.")
        percent = simulated_refund_percent(order.session_starts_at, now)
        if percent is None:
            return self._preview(order, count, False, None, None,
                "Faltam menos de 24h para a sessão: o cancelamento não é mais permitido pelo site. Procure o teatro.")
        cents = _refund_cents(order, percent)
        return self._preview(order, count, True, percent, cents,
            f"Os {count} ingresso(s) serão invalidados e as vagas liberadas. Reembolso simulado de {percent}% "
            f"(R$ {cents / 100:.2f}). {SIMULATION_NOTE}")

    @staticmethod
    def _preview(order, count, can, percent, cents, message) -> CancellationPreviewResponse:
        return CancellationPreviewResponse(order_id=order.id, can_cancel=can, order_status=order.status.value,
            ticket_count=count, refund_percent=percent, refund_cents=cents, message=message)

    async def cancel(self, order_id: UUID, user: User, reason: str | None) -> OrderResponse:
        snapshot = await self._purchases._owned_order(order_id, user.id)
        await lock_booking_session(self._db, snapshot.session_id)
        order = await self._db.scalar(select(PaymentOrder).where(
            PaymentOrder.id == order_id, PaymentOrder.user_id == user.id,
        ).with_for_update().execution_options(populate_existing=True))
        if order is None:
            raise NotFoundError("Pedido não encontrado.")
        if order.status is OrderStatus.CANCELLED and order.cancelled_at is not None:
            return await self._purchases._response(order)  # repeated request: nothing changes
        reservation = await self._db.scalar(select(Reservation).where(Reservation.id == order.reservation_id)
            .with_for_update().execution_options(populate_existing=True))
        now = await self._purchases._now()
        text = (reason or "").strip() or BUYER_REASON

        if order.status is OrderStatus.PENDING:
            await self._cancel_pending(order, reservation, user, text, now)
        elif order.status is OrderStatus.PAID:
            if order.session_starts_at <= now:
                raise ConflictError("A sessão já começou.")
            used = await self._db.scalar(select(func.count()).select_from(Ticket).where(
                Ticket.order_id == order.id, Ticket.status == TicketStatus.USED))
            if used:
                raise ConflictError("Há ingressos já utilizados no check-in.")
            percent = simulated_refund_percent(order.session_starts_at, now)
            if percent is None:
                raise ConflictError("Cancelamentos com menos de 24h da sessão não são permitidos pelo site. Procure o teatro.")
            await cancel_paid_order(self._db, order, reservation, now=now, origin=CancelOrigin.BUYER,
                                    by=user.id, reason=text, percent=percent)
        else:
            raise ConflictError("Este pedido não pode ser cancelado.")
        await self._db.flush()
        return await self._purchases._response(order)

    async def _cancel_pending(self, order: PaymentOrder, reservation: Reservation | None, user: User, reason: str, now: datetime) -> None:
        live = (reservation is not None and reservation.status is ReservationStatus.OPEN
                and reservation.expires_at > now and order.expires_at > now)
        if not live:
            await self._purchases._expire(order, reservation, now)
            return
        reservation.cancel(now)
        await ReservationRepository(self._db).save(reservation)
        order.status = OrderStatus.CANCELLED
        order.resolved_at = order.cancelled_at = now
        order.cancelled_by = user.id
        order.cancel_origin = CancelOrigin.BUYER
        order.cancel_reason = reason

    async def list_all(self, pagination: PaginationParams, *, status: str | None = None, session_id: UUID | None = None) -> Page[AdminOrderResponse]:
        stmt = select(PaymentOrder, User).join(User, User.id == PaymentOrder.user_id)
        count = select(func.count()).select_from(PaymentOrder)
        if status:
            try:
                wanted = OrderStatus(status)
            except ValueError:
                raise DomainError("Status de pedido inválido.")
            stmt, count = stmt.where(PaymentOrder.status == wanted), count.where(PaymentOrder.status == wanted)
        if session_id:
            stmt, count = stmt.where(PaymentOrder.session_id == session_id), count.where(PaymentOrder.session_id == session_id)
        rows = (await self._db.execute(stmt.order_by(PaymentOrder.created_at.desc(), PaymentOrder.id)
            .offset((pagination.page - 1) * pagination.size).limit(pagination.size))).all()
        items = []
        for order, buyer in rows:
            base = await self._purchases._response(order)
            items.append(AdminOrderResponse(**base.model_dump(), buyer_name=buyer.name, buyer_email=buyer.email))
        return Page(items=items, total=await self._db.scalar(count), page=pagination.page, size=pagination.size)

    async def check_in(self, code: str) -> CheckInResponse:
        ticket = await self._db.scalar(select(Ticket).where(Ticket.code == code.strip())
            .with_for_update().execution_options(populate_existing=True))
        if ticket is None:
            raise NotFoundError("Ingresso não encontrado.")
        if ticket.status is TicketStatus.CANCELLED:
            raise ConflictError("Ingresso cancelado: entrada não permitida.")
        if ticket.status is TicketStatus.USED:
            raise ConflictError("Ingresso já utilizado.")
        order = await self._db.get(PaymentOrder, ticket.order_id)
        if order is None or order.status is not OrderStatus.PAID:
            raise ConflictError("Ingresso não está válido: entrada não permitida.")
        ticket.status = TicketStatus.USED
        ticket.checked_in_at = datetime.now(timezone.utc)
        await self._db.flush()
        return CheckInResponse(code=ticket.code, ticket_type=ticket.ticket_type, status="used",
            checked_in_at=ticket.checked_in_at.isoformat(), show_title=order.show_title,
            session_starts_at=order.session_starts_at.isoformat(), venue=order.venue)

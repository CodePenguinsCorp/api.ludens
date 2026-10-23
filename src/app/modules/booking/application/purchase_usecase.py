from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.booking.application.purchase_schemas import OrderResponse, TicketResponse
from app.modules.booking.domain.purchase import OrderStatus, PaymentOrder, Ticket, TicketStatus
from app.modules.booking.domain.reservation import Reservation, ReservationStatus
from app.modules.booking.infrastructure.reservation_repository import ReservationRepository
from app.modules.catalog.dependencies import lock_booking_session
from app.modules.catalog.domain.aggregates import Session
from app.modules.identity.domain.aggregates import User
from app.outbox.models import Event


class PurchaseUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._reservations = ReservationRepository(db)

    async def start(self, reservation_id: UUID, user: User) -> OrderResponse:
        if user.is_admin:
            raise ForbiddenError("Administradores não podem iniciar uma compra.")
        snapshot = await self._owned_reservation(reservation_id, user.id)
        bookable = await lock_booking_session(self._db, snapshot.session_id)
        existing = await self._order_for_reservation(snapshot.id, lock=True)
        reservation = await self._db.scalar(select(Reservation).where(
            Reservation.id == reservation_id, Reservation.user_id == user.id,
        ).with_for_update().execution_options(populate_existing=True))
        if reservation is None:
            raise NotFoundError("Reserva não encontrada.")

        now = await self._now()
        if existing is not None:
            if existing.status is OrderStatus.PENDING:
                if reservation.status is not ReservationStatus.OPEN or reservation.expires_at <= now or existing.expires_at <= now:
                    await self._expire(existing, reservation, now)
            elif existing.status is not OrderStatus.PAID:
                # Orders closed earlier cannot be resumed; the buyer needs a new reservation.
                raise ConflictError("A reserva não está mais válida. Faça uma nova reserva para continuar.")
            return await self._response(existing)

        if not bookable.on_sale or bookable.starts_at <= now or reservation.status is not ReservationStatus.OPEN or reservation.expires_at <= now:
            raise ConflictError("A reserva não está mais válida. Faça uma nova reserva para continuar.")

        order = PaymentOrder(
            reservation_id=reservation.id,
            user_id=user.id,
            session_id=reservation.session_id,
            show_title=reservation.show_title,
            session_starts_at=reservation.starts_at,
            venue=reservation.venue,
            full_quantity=reservation.full_quantity,
            half_quantity=reservation.half_quantity,
            full_price_cents=reservation.full_price_cents,
            half_price_cents=reservation.half_price_cents,
            amount_cents=reservation.total_cents,
            status=OrderStatus.PENDING,
            expires_at=min(reservation.expires_at, bookable.starts_at),
        )
        try:
            async with self._db.begin_nested():
                self._db.add(order)
                await self._db.flush()
        except IntegrityError:
            order = await self._order_for_reservation(reservation.id)
            if order is None:
                raise
        return await self._response(order)

    async def simulate(self, order_id: UUID, outcome: str, user: User) -> OrderResponse:
        snapshot = await self._owned_order(order_id, user.id)
        bookable = await lock_booking_session(self._db, snapshot.session_id)
        order = await self._db.scalar(select(PaymentOrder).where(
            PaymentOrder.id == order_id, PaymentOrder.user_id == user.id,
        ).with_for_update().execution_options(populate_existing=True))
        if order is None:
            raise NotFoundError("Pedido não encontrado.")
        reservation = await self._db.scalar(select(Reservation).where(
            Reservation.id == order.reservation_id,
        ).with_for_update().execution_options(populate_existing=True))
        now = await self._now()

        if order.status is not OrderStatus.PENDING:
            # Repeated approval returns the already-issued order without duplicating tickets.
            if outcome == "approved" and order.status is OrderStatus.PAID:
                return await self._response(order)
            if outcome == "declined" and order.status is OrderStatus.DECLINED:
                return await self._response(order)
            if order.status is OrderStatus.EXPIRED:
                raise ConflictError("O prazo de confirmação do pedido expirou.")
            raise ConflictError("O pedido já foi finalizado.")

        if reservation is None or reservation.status is not ReservationStatus.OPEN or reservation.expires_at <= now or order.expires_at <= now:
            await self._expire(order, reservation, now)
            return await self._response(order)
        if not bookable.on_sale:
            raise ConflictError("Esta sessão não está mais disponível para compra.")

        if outcome == "expired":
            reservation.expire(now, force=True)
            await self._reservations.save(reservation)
            order.status = OrderStatus.EXPIRED
            order.resolved_at = now
            return await self._response(order)

        if outcome == "declined":
            reservation.cancel(now)
            await self._reservations.save(reservation)
            order.status = OrderStatus.DECLINED
            order.resolved_at = now
            return await self._response(order)

        reservation.confirm(now)
        await self._reservations.save(reservation)
        order.status = OrderStatus.PAID
        order.resolved_at = now
        tickets = [
            Ticket(order_id=order.id, ticket_type="full", price_cents=order.full_price_cents)
            for _ in range(order.full_quantity)
        ] + [
            Ticket(order_id=order.id, ticket_type="half", price_cents=order.half_price_cents)
            for _ in range(order.half_quantity)
        ]
        self._db.add_all(tickets)
        await self._db.flush()
        await self._queue_confirmation(order, user.email, tickets)
        return self._response_with_tickets(order, tickets)

    async def get(self, order_id: UUID, user: User) -> OrderResponse:
        order = await self._owned_order(order_id, user.id)
        if order.status is OrderStatus.PENDING:
            await self._db.scalar(select(Session).where(Session.id == order.session_id).with_for_update())
            order = await self._db.scalar(select(PaymentOrder).where(
                PaymentOrder.id == order_id, PaymentOrder.user_id == user.id,
            ).with_for_update().execution_options(populate_existing=True))
            if order is None:
                raise NotFoundError("Pedido não encontrado.")
            reservation = await self._db.scalar(select(Reservation).where(
                Reservation.id == order.reservation_id,
            ).with_for_update().execution_options(populate_existing=True))
            now = await self._now()
            if reservation is None or reservation.status is not ReservationStatus.OPEN or reservation.expires_at <= now or order.expires_at <= now:
                await self._expire(order, reservation, now)
        return await self._response(order)

    async def list(self, user: User) -> list[OrderResponse]:
        orders = list(await self._db.scalars(select(PaymentOrder).where(
            PaymentOrder.user_id == user.id,
        ).order_by(PaymentOrder.created_at.desc(), PaymentOrder.id)))
        results = []
        for order in orders:
            results.append(await self._response(order))
        return results

    async def _owned_reservation(self, reservation_id: UUID, user_id: UUID) -> Reservation:
        reservation = await self._db.scalar(select(Reservation).where(
            Reservation.id == reservation_id, Reservation.user_id == user_id,
        ))
        if reservation is None:
            raise NotFoundError("Reserva não encontrada.")
        return reservation

    async def _owned_order(self, order_id: UUID, user_id: UUID) -> PaymentOrder:
        order = await self._db.scalar(select(PaymentOrder).where(
            PaymentOrder.id == order_id, PaymentOrder.user_id == user_id,
        ))
        if order is None:
            raise NotFoundError("Pedido não encontrado.")
        return order

    async def _order_for_reservation(self, reservation_id: UUID, *, lock: bool = False) -> PaymentOrder | None:
        stmt = select(PaymentOrder).where(PaymentOrder.reservation_id == reservation_id)
        if lock:
            stmt = stmt.with_for_update()
        return await self._db.scalar(stmt)

    async def _tickets(self, order_id: UUID) -> list[Ticket]:
        return list(await self._db.scalars(select(Ticket).where(
            Ticket.order_id == order_id,
        ).order_by(Ticket.created_at, Ticket.id)))

    async def _response(self, order: PaymentOrder) -> OrderResponse:
        return self._response_with_tickets(order, await self._tickets(order.id))

    @staticmethod
    def _response_with_tickets(order: PaymentOrder, tickets: list[Ticket]) -> OrderResponse:
        return OrderResponse(
            id=order.id, reservation_id=order.reservation_id, session_id=order.session_id,
            show_title=order.show_title, session_starts_at=order.session_starts_at, venue=order.venue,
            full_quantity=order.full_quantity, half_quantity=order.half_quantity,
            full_price_cents=order.full_price_cents, half_price_cents=order.half_price_cents,
            amount_cents=order.amount_cents, status=order.status.value,
            expires_at=order.expires_at, resolved_at=order.resolved_at,
            tickets=[TicketResponse(id=t.id, ticket_type=t.ticket_type, price_cents=t.price_cents,
                                    code=t.code, status=t.status.value) for t in tickets],
        )

    async def _expire(self, order: PaymentOrder, reservation: Reservation | None, now: datetime) -> None:
        if order.status is not OrderStatus.PENDING:
            return
        if reservation is not None and reservation.status is ReservationStatus.OPEN and (reservation.expires_at <= now or order.expires_at <= now):
            reservation.expire(now, force=True)
            await self._reservations.save(reservation)
        order.status = OrderStatus.EXPIRED if reservation is None or reservation.status is ReservationStatus.EXPIRED or reservation.expires_at <= now or order.expires_at <= now else OrderStatus.CANCELLED
        order.resolved_at = now

    async def _queue_confirmation(self, order: PaymentOrder, email: str, tickets: list[Ticket]) -> None:
        self._db.add(Event(
            aggregate_id=str(order.id),
            event_type="OrderPaid",
            payload={
                "email": email,
                "order_id": str(order.id),
                "show_title": order.show_title,
                "session_starts_at": order.session_starts_at.isoformat(),
                "venue": order.venue,
                "amount_cents": order.amount_cents,
                "tickets": [
                    {"ticket_type": ticket.ticket_type, "code": ticket.code, "price_cents": ticket.price_cents}
                    for ticket in tickets
                ],
            },
        ))

    async def _now(self) -> datetime:
        value = await self._db.scalar(select(func.clock_timestamp()))
        return value or datetime.now(timezone.utc)

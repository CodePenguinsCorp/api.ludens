from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, or_, select

from app.core.infrastructure.repositories import AggregateRepository
from app.modules.booking.domain.reservation import Reservation, ReservationStatus
from app.modules.booking.domain.purchase import OrderStatus, PaymentOrder


class ReservationRepository(AggregateRepository[Reservation]):
    model = Reservation

    async def find_request(self, user_id: UUID, key: UUID) -> Reservation | None:
        return await self._session.scalar(select(Reservation).where(
            Reservation.user_id == user_id, Reservation.idempotency_key == key,
        ))

    async def find_owned(self, reservation_id: UUID, user_id: UUID, *, lock: bool = True) -> Reservation | None:
        stmt = select(Reservation).where(Reservation.id == reservation_id, Reservation.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt.execution_options(populate_existing=True))

    async def list_owned(self, user_id: UUID, *, page: int, size: int) -> tuple[list[Reservation], int]:
        total = await self._session.scalar(select(func.count()).select_from(Reservation).where(Reservation.user_id == user_id))
        result = await self._session.scalars(select(Reservation).where(Reservation.user_id == user_id)
            .order_by(Reservation.created_at.desc(), Reservation.id).offset((page - 1) * size).limit(size))
        return list(result), total or 0

    @staticmethod
    def committed(now: datetime):
        return or_(Reservation.status == ReservationStatus.CONFIRMED,
            (Reservation.status == ReservationStatus.OPEN) & (Reservation.expires_at > now))

    async def count_cpf(self, session_id: UUID, cpf: str, now: datetime) -> int:
        return await self._session.scalar(select(func.coalesce(func.sum(Reservation.full_quantity + Reservation.half_quantity), 0))
            .where(Reservation.session_id == session_id, Reservation.buyer_cpf == cpf, self.committed(now))) or 0

    async def counts(self, session_ids: list[UUID], now: datetime) -> dict[UUID, tuple[int, int]]:
        if not session_ids:
            return {}
        quantity = Reservation.full_quantity + Reservation.half_quantity
        result = await self._session.execute(select(Reservation.session_id,
            func.sum(case((Reservation.status == ReservationStatus.CONFIRMED, quantity), else_=0)),
            func.sum(case(((Reservation.status == ReservationStatus.OPEN) & (Reservation.expires_at > now), quantity), else_=0)))
            .where(Reservation.session_id.in_(session_ids)).group_by(Reservation.session_id))
        return {session_id: (sold, held) for session_id, sold, held in result}

    async def expire_due(self, now: datetime, *, user_id: UUID | None = None, limit: int = 100) -> int:
        order_stmt = select(PaymentOrder).where(
            PaymentOrder.status == OrderStatus.PENDING, PaymentOrder.expires_at <= now,
        )
        if user_id is not None:
            order_stmt = order_stmt.where(PaymentOrder.user_id == user_id)
        pending_orders = list(await self._session.scalars(
            order_stmt.order_by(PaymentOrder.expires_at, PaymentOrder.id).limit(limit)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True)
        ))
        expired_order_count = 0
        for order in pending_orders:
            reservation = await self._session.scalar(select(Reservation).where(
                Reservation.id == order.reservation_id,
            ).with_for_update().execution_options(populate_existing=True))
            if reservation is not None and reservation.status == ReservationStatus.OPEN:
                reservation.expire(now, force=True)
                await self.save(reservation)
            order.status = OrderStatus.EXPIRED
            order.resolved_at = now
            expired_order_count += 1

        stmt = select(Reservation).where(Reservation.status == ReservationStatus.OPEN, Reservation.expires_at <= now)
        if user_id is not None:
            stmt = stmt.where(Reservation.user_id == user_id)
        rows = await self._session.scalars(stmt.order_by(Reservation.expires_at).limit(limit)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        reservations = list(rows)
        for reservation in reservations:
            reservation.expire(now)
            await self.save(reservation)
        await self._session.flush()
        return len(reservations) + expired_order_count

    async def cancel_for_session(self, session_id: UUID, now: datetime) -> None:
        orders = await self._session.scalars(select(PaymentOrder).join(
            Reservation, PaymentOrder.reservation_id == Reservation.id,
        ).where(
            Reservation.session_id == session_id, PaymentOrder.status == OrderStatus.PENDING,
        ).order_by(PaymentOrder.id).with_for_update(of=PaymentOrder))
        for order in orders:
            order.status = OrderStatus.CANCELLED
            order.resolved_at = now
        rows = await self._session.scalars(select(Reservation).where(
            Reservation.session_id == session_id, Reservation.status == ReservationStatus.OPEN,
        ).order_by(Reservation.id).with_for_update().execution_options(populate_existing=True))
        for reservation in rows:
            reservation.cancel(now)
            await self.save(reservation)
        await self._session.flush()

    async def finish_pending_order(self, reservation: Reservation, now: datetime) -> None:
        order = await self._session.scalar(select(PaymentOrder).where(
            PaymentOrder.reservation_id == reservation.id,
            PaymentOrder.status == OrderStatus.PENDING,
        ).with_for_update())
        if order is not None:
            order.status = OrderStatus.EXPIRED if reservation.status == ReservationStatus.EXPIRED else OrderStatus.CANCELLED
            order.resolved_at = now

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.domain import AggregateRoot, DomainEvent, Model
from app.core.domain.errors import ConflictError, DomainError
from app.modules.booking.domain.events import (
    ReservationCancelled, ReservationConfirmed, ReservationCreated, ReservationExpired,
)


class ReservationStatus(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Reservation(AggregateRoot, Model):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_reservation_user_key"),
        CheckConstraint("full_quantity >= 0 AND half_quantity >= 0 AND full_quantity + half_quantity > 0", name="ck_reservation_quantities"),
        CheckConstraint("full_price_cents >= 0 AND half_price_cents >= 0", name="ck_reservation_prices"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    buyer_cpf: Mapped[str] = mapped_column(String(11), nullable=False)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    full_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    half_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    full_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    half_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    show_title: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str] = mapped_column(String(200), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[ReservationStatus] = mapped_column(
        SAEnum(ReservationStatus, native_enum=False, length=20, values_callable=lambda e: [s.value for s in e]),
        nullable=False,
    )

    @property
    def quantity(self) -> int:
        return self.full_quantity + self.half_quantity

    @property
    def total_cents(self) -> int:
        return self.full_quantity * self.full_price_cents + self.half_quantity * self.half_price_cents

    @classmethod
    def create(cls, *, now: datetime, **values) -> "Reservation":
        if values["full_quantity"] < 0 or values["half_quantity"] < 0 or values["full_quantity"] + values["half_quantity"] < 1:
            raise DomainError("Selecione pelo menos um ingresso.")
        reservation = cls()
        reservation.raise_event(lambda v: ReservationCreated(version=v, id=uuid4(), timestamp=now, **values))
        return reservation

    def expire(self, now: datetime, *, force: bool = False) -> None:
        if self.status is ReservationStatus.OPEN and (force or self.expires_at <= now):
            self.raise_event(lambda v: ReservationExpired(version=v, id=self.id, timestamp=now))

    def cancel(self, now: datetime) -> None:
        self.expire(now)
        if self.status is ReservationStatus.CONFIRMED:
            raise ConflictError("Uma reserva confirmada deve seguir o fluxo de reembolso.")
        if self.status is ReservationStatus.OPEN:
            self.raise_event(lambda v: ReservationCancelled(version=v, id=self.id, timestamp=now))

    def confirm(self, now: datetime) -> None:
        if self.status is not ReservationStatus.OPEN or self.expires_at <= now:
            raise ConflictError("A reserva não está mais aberta para confirmação.")
        self.raise_event(lambda v: ReservationConfirmed(version=v, id=self.id, timestamp=now))

    def _apply(self, event: DomainEvent) -> None:
        if isinstance(event, ReservationCreated):
            for key, value in vars(event).items():
                if key not in {"version", "timestamp"}:
                    setattr(self, key, value)
            self.created_at = event.timestamp
            self.is_active = True
            self.status = ReservationStatus.OPEN
        elif isinstance(event, ReservationCancelled):
            self.status = ReservationStatus.CANCELLED
        elif isinstance(event, ReservationExpired):
            self.status = ReservationStatus.EXPIRED
        elif isinstance(event, ReservationConfirmed):
            self.status = ReservationStatus.CONFIRMED
        self.updated_at = event.timestamp

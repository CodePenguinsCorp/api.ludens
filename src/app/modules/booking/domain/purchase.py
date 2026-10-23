from __future__ import annotations

import secrets
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.domain import Model


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TicketStatus(str, Enum):
    VALID = "valid"
    CANCELLED = "cancelled"


class PaymentOrder(Model):
    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint("reservation_id", name="uq_payment_orders_reservation"),
        CheckConstraint("amount_cents >= 0", name="ck_payment_orders_amount"),
    )

    reservation_id: Mapped[UUID] = mapped_column(ForeignKey("reservations.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"), nullable=False, index=True)
    show_title: Mapped[str] = mapped_column(String(200), nullable=False)
    session_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str] = mapped_column(String(200), nullable=False)
    full_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    half_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    full_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    half_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, native_enum=False, length=20, values_callable=lambda e: [s.value for s in e]),
        nullable=False,
        default=OrderStatus.PENDING,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Ticket(Model):
    __tablename__ = "tickets"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("payment_orders.id"), nullable=False, index=True)
    ticket_type: Mapped[str] = mapped_column(String(8), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True,
                                      default=lambda: f"DEMO-{secrets.token_hex(12).upper()}")
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, native_enum=False, length=20, values_callable=lambda e: [s.value for s in e]),
        nullable=False,
        default=TicketStatus.VALID,
    )

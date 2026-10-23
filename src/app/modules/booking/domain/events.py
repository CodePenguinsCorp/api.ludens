from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.core.domain import DomainEvent


@dataclass(frozen=True)
class ReservationCreated(DomainEvent):
    id: UUID = field(kw_only=True)
    user_id: UUID = field(kw_only=True)
    buyer_cpf: str = field(kw_only=True)
    session_id: UUID = field(kw_only=True)
    idempotency_key: UUID = field(kw_only=True)
    full_quantity: int = field(kw_only=True)
    half_quantity: int = field(kw_only=True)
    full_price_cents: int = field(kw_only=True)
    half_price_cents: int = field(kw_only=True)
    show_title: str = field(kw_only=True)
    starts_at: datetime = field(kw_only=True)
    venue: str = field(kw_only=True)
    expires_at: datetime = field(kw_only=True)


@dataclass(frozen=True)
class ReservationCancelled(DomainEvent):
    id: UUID = field(kw_only=True)


@dataclass(frozen=True)
class ReservationExpired(DomainEvent):
    id: UUID = field(kw_only=True)


@dataclass(frozen=True)
class ReservationConfirmed(DomainEvent):
    id: UUID = field(kw_only=True)

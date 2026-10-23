from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SimulatedPaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["approved", "declined", "expired"]


class TicketResponse(BaseModel):
    id: UUID
    ticket_type: Literal["full", "half"]
    price_cents: int
    code: str
    status: Literal["valid", "cancelled"]
    is_demonstration: Literal[True] = True


class OrderResponse(BaseModel):
    id: UUID
    reservation_id: UUID
    session_id: UUID
    show_title: str
    session_starts_at: datetime
    venue: str
    full_quantity: int
    half_quantity: int
    full_price_cents: int
    half_price_cents: int
    amount_cents: int
    status: Literal["pending", "paid", "declined", "expired", "cancelled"]
    expires_at: datetime
    resolved_at: datetime | None
    tickets: list[TicketResponse]
    is_demonstration: Literal[True] = True

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    idempotency_key: UUID
    full_quantity: int = Field(ge=0, le=6, strict=True)
    half_quantity: int = Field(ge=0, le=6, strict=True)


class ReservationResponse(BaseModel):
    id: UUID
    session_id: UUID
    show_title: str
    starts_at: datetime
    venue: str
    full_quantity: int
    half_quantity: int
    full_price_cents: int
    half_price_cents: int
    total_cents: int
    status: Literal["open", "confirmed", "expired", "cancelled"]
    created_at: datetime
    expires_at: datetime
    server_now: datetime


class RebookingSessionResponse(BaseModel):
    id: UUID
    starts_at: datetime
    venue: str
    available_count: int


class RebookingOptionsResponse(BaseModel):
    sessions: list[RebookingSessionResponse]
    server_now: datetime

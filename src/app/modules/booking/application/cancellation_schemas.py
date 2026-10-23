from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.booking.application.purchase_schemas import OrderResponse


class CancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=300)


class CancellationPreviewResponse(BaseModel):
    order_id: UUID
    can_cancel: bool
    order_status: Literal["pending", "paid", "declined", "expired", "cancelled"]
    ticket_count: int
    refund_percent: int | None
    refund_cents: int | None
    message: str
    is_refund_simulated: Literal[True] = True


class AdminOrderResponse(OrderResponse):
    buyer_name: str
    buyer_email: str


class CheckInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=48)


class CheckInResponse(BaseModel):
    code: str
    ticket_type: Literal["full", "half"]
    status: Literal["used"]
    checked_in_at: str
    show_title: str
    session_starts_at: str
    venue: str

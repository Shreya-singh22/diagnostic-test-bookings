from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import BookingStatus, PaymentStatus
from app.schemas.common import ORMModel


class PaymentCreate(BaseModel):
    booking_id: int = Field(gt=0)
    simulate_outcome: Literal["SUCCESS", "FAILED"] | None = Field(
        None,
        description="Force the simulated result. If omitted, the mock gateway succeeds "
        "with probability PAYMENT_SUCCESS_RATE.",
    )


class PaymentOut(ORMModel):
    id: int
    booking_id: int
    amount: Decimal
    status: PaymentStatus
    provider_reference: str
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class PaymentResult(BaseModel):
    payment: PaymentOut
    booking_status: BookingStatus


class WebhookPayload(BaseModel):
    """Event sent by the (simulated) payment provider."""

    event_id: str = Field(min_length=1, max_length=100, examples=["evt_7f3a9c"])
    event_type: Literal["payment.succeeded", "payment.failed"]
    payment_reference: str = Field(min_length=1, max_length=64, examples=["pay_1a2b3c"])
    booking_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    failure_reason: str | None = Field(None, max_length=255)


class WebhookAck(BaseModel):
    event_id: str
    status: Literal["processed", "duplicate"]
    result: str | None
    booking_status: BookingStatus | None = None
    payment_status: PaymentStatus | None = None

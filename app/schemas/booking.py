from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import BookingStatus
from app.schemas.common import ORMModel


class BookingCreate(BaseModel):
    centre_id: int = Field(gt=0)
    test_id: int = Field(gt=0)
    appointment_at: datetime = Field(
        description="ISO-8601 datetime. Must be in the future. Naive values are treated as UTC.",
        examples=["2030-01-15T09:30:00+05:30"],
    )

    @field_validator("appointment_at")
    @classmethod
    def must_be_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value <= datetime.now(timezone.utc):
            raise ValueError("appointment_at must be in the future")
        # Normalise to UTC so equal instants compare equal (duplicate-slot check).
        return value.astimezone(timezone.utc)


class BookingCentreOut(ORMModel):
    id: int
    name: str
    city: str


class BookingTestOut(ORMModel):
    id: int
    code: str
    name: str


class BookingOut(ORMModel):
    id: int
    user_id: int
    centre: BookingCentreOut
    test: BookingTestOut
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus
    created_at: datetime
    updated_at: datetime

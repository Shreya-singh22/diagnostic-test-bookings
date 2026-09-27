from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models import BookingStatus
from app.schemas.booking import BookingCreate, BookingOut
from app.schemas.common import Page, PageParams
from app.schemas.payment import PaymentOut
from app.services import booking_service, payment_service

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post(
    "",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Centre not found"},
        409: {"description": "Duplicate active booking for the same slot"},
        422: {"description": "Invalid input or test not offered at this centre"},
    },
)
def create_booking(data: BookingCreate, db: DbSession, user: CurrentUser) -> BookingOut:
    return booking_service.create_booking(db, user, data)


@router.get("", response_model=Page[BookingOut])
def list_bookings(
    db: DbSession,
    user: CurrentUser,
    page: Annotated[PageParams, Depends()],
    status: Annotated[BookingStatus | None, Query()] = None,
) -> Page[BookingOut]:
    """The caller's bookings, newest first (admins see all bookings)."""
    return booking_service.list_bookings(db, user, page, status)


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: int, db: DbSession, user: CurrentUser) -> BookingOut:
    return booking_service.get_booking(db, user, booking_id)


@router.post(
    "/{booking_id}/cancel",
    response_model=BookingOut,
    responses={409: {"description": "Already cancelled or appointment in the past"}},
)
def cancel_booking(booking_id: int, db: DbSession, user: CurrentUser) -> BookingOut:
    return booking_service.cancel_booking(db, user, booking_id)


@router.get("/{booking_id}/payments", response_model=list[PaymentOut])
def list_booking_payments(booking_id: int, db: DbSession, user: CurrentUser) -> list[PaymentOut]:
    return payment_service.list_booking_payments(db, user, booking_id)

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.base import ensure_aware, utcnow
from app.models import Booking, BookingStatus, CentreTest, DiagnosticCentre, User
from app.schemas.booking import BookingCreate, BookingOut
from app.schemas.common import Page, PageParams

logger = logging.getLogger(__name__)

# The booking state machine. Anything not listed here is rejected.
#   PENDING  -> CONFIRMED | FAILED | CANCELLED
#   FAILED   -> CONFIRMED | FAILED | CANCELLED   (payment may be retried)
#   CONFIRMED-> CANCELLED
#   CANCELLED   (terminal)
ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED},
    BookingStatus.FAILED: {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED},
    BookingStatus.CONFIRMED: {BookingStatus.CANCELLED},
    BookingStatus.CANCELLED: set(),
}

PAYABLE_STATUSES = {BookingStatus.PENDING, BookingStatus.FAILED}


def can_transition(booking: Booking, new_status: BookingStatus) -> bool:
    return new_status in ALLOWED_TRANSITIONS[booking.status]


def transition(booking: Booking, new_status: BookingStatus) -> None:
    if not can_transition(booking, new_status):
        raise ConflictError(
            f"Booking cannot move from {booking.status.value} to {new_status.value}",
            code="INVALID_STATUS_TRANSITION",
        )
    logger.info(
        "booking_status_changed",
        extra={"booking_id": booking.id, "from": booking.status.value, "to": new_status.value},
    )
    booking.status = new_status


def create_booking(db: Session, user: User, data: BookingCreate) -> Booking:
    centre = db.get(DiagnosticCentre, data.centre_id)
    if centre is None or not centre.is_active:
        raise NotFoundError(f"Centre {data.centre_id} not found", code="CENTRE_NOT_FOUND")
    offering = db.scalar(
        select(CentreTest).where(
            CentreTest.centre_id == data.centre_id,
            CentreTest.test_id == data.test_id,
            CentreTest.is_active.is_(True),
        )
    )
    if offering is None:
        raise ValidationError(
            f"Test {data.test_id} is not offered at centre {data.centre_id}",
            code="TEST_NOT_OFFERED",
        )

    duplicate = db.scalar(
        select(Booking.id).where(
            Booking.user_id == user.id,
            Booking.centre_id == data.centre_id,
            Booking.test_id == data.test_id,
            Booking.appointment_at == data.appointment_at,
            Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
        )
    )
    if duplicate is not None:
        raise _duplicate_error(duplicate)

    booking = Booking(
        user_id=user.id,
        centre_id=data.centre_id,
        test_id=data.test_id,
        appointment_at=data.appointment_at,
        amount=offering.price,  # server-side price; never trusted from the client
        status=BookingStatus.PENDING,
    )
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:  # lost a race against an identical request
        db.rollback()
        raise _duplicate_error(None)
    logger.info("booking_created", extra={"booking_id": booking.id, "user_id": user.id})
    return booking


def _duplicate_error(existing_id: int | None) -> ConflictError:
    return ConflictError(
        "You already have an active booking for this test, centre and time",
        code="DUPLICATE_BOOKING",
        details={"booking_id": existing_id} if existing_id else None,
    )


def get_booking(db: Session, user: User, booking_id: int, *, for_update: bool = False) -> Booking:
    """Fetch a booking the user may access.

    Another user's booking is reported as not found (rather than forbidden) so
    that booking ids cannot be enumerated. Admins can access every booking.
    """
    query = select(Booking).where(Booking.id == booking_id)
    if not user.is_admin:
        query = query.where(Booking.user_id == user.id)
    if for_update:
        query = query.with_for_update(of=Booking)
    booking = db.scalar(query)
    if booking is None:
        raise NotFoundError(f"Booking {booking_id} not found", code="BOOKING_NOT_FOUND")
    return booking


def list_bookings(
    db: Session, user: User, page: PageParams, status: BookingStatus | None
) -> Page[BookingOut]:
    query = select(Booking)
    if not user.is_admin:
        query = query.where(Booking.user_id == user.id)
    if status:
        query = query.where(Booking.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(Booking.created_at.desc(), Booking.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[BookingOut](
        items=[BookingOut.model_validate(b) for b in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


def cancel_booking(db: Session, user: User, booking_id: int) -> Booking:
    booking = get_booking(db, user, booking_id, for_update=True)
    if booking.status == BookingStatus.CANCELLED:
        raise ConflictError("Booking is already cancelled", code="BOOKING_ALREADY_CANCELLED")
    if ensure_aware(booking.appointment_at) <= utcnow():
        raise ConflictError(
            "Cannot cancel a booking whose appointment time has passed",
            code="APPOINTMENT_IN_PAST",
        )
    if booking.status == BookingStatus.CONFIRMED:
        # Refunds are out of scope; we log so ops could reconcile.
        logger.info("confirmed_booking_cancelled_refund_due", extra={"booking_id": booking.id})
    transition(booking, BookingStatus.CANCELLED)
    db.commit()
    return booking

"""Simulated payment gateway and the rules for applying a payment outcome."""

import logging
import random
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.db.base import ensure_aware, utcnow
from app.models import Booking, BookingStatus, Payment, PaymentStatus, User
from app.schemas.payment import PaymentCreate
from app.services import booking_service

logger = logging.getLogger(__name__)

SIMULATED_DECLINE_REASON = "Card declined by issuer (simulated)"


def apply_payment_outcome(
    booking: Booking, payment: Payment, new_status: PaymentStatus, failure_reason: str | None = None
) -> str:
    """Move `payment` to `new_status` and update `booking` to match.

    Returns a short result label. The rules keep state consistent no matter how
    many times, or in which order, outcomes arrive:

    * SUCCESS is terminal for a payment: a later FAILED never downgrades it.
    * Re-applying the current status is a no-op.
    * A successful payment confirms a PENDING/FAILED booking. It never
      resurrects a CANCELLED booking; that case is logged for a refund.
    * A failed payment only marks a booking FAILED if it isn't already
      CONFIRMED (e.g. by another payment) or CANCELLED.
    """
    if payment.status == new_status:
        return "no_change"
    if payment.status == PaymentStatus.SUCCESS:
        logger.warning(
            "stale_payment_event_ignored",
            extra={"payment_id": payment.id, "current": payment.status.value, "incoming": new_status.value},
        )
        return "ignored_stale"

    payment.status = new_status
    payment.failure_reason = failure_reason if new_status == PaymentStatus.FAILED else None

    target = BookingStatus.CONFIRMED if new_status == PaymentStatus.SUCCESS else BookingStatus.FAILED
    if booking.status == target:
        return "applied"
    if booking_service.can_transition(booking, target):
        booking_service.transition(booking, target)
    elif new_status == PaymentStatus.SUCCESS:
        # Money was taken for a booking that is cancelled or already paid.
        logger.warning(
            "payment_succeeded_refund_required",
            extra={"booking_id": booking.id, "booking_status": booking.status.value},
        )
        return "applied_refund_required"
    return "applied"


def _simulate_gateway(forced: str | None) -> PaymentStatus:
    if forced:
        return PaymentStatus(forced)
    succeeded = random.random() < get_settings().payment_success_rate
    return PaymentStatus.SUCCESS if succeeded else PaymentStatus.FAILED


def _find_by_idempotency_key(db: Session, user: User, key: str) -> Payment | None:
    return db.scalar(
        select(Payment).where(Payment.user_id == user.id, Payment.idempotency_key == key)
    )


def _replay(existing: Payment, booking_id: int) -> tuple[Payment, BookingStatus, bool]:
    if existing.booking_id != booking_id:
        raise ConflictError(
            "Idempotency-Key was already used for a different booking",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    return existing, existing.booking.status, True


def create_payment(
    db: Session, user: User, data: PaymentCreate, idempotency_key: str | None
) -> tuple[Payment, BookingStatus, bool]:
    """Charge a booking through the mock gateway.

    Returns (payment, booking_status, replayed). `replayed` is True when an
    earlier request with the same Idempotency-Key is being returned.
    """
    # Lock the booking row first: concurrent payment attempts for the same
    # booking are serialised, so it can never be charged twice.
    booking = booking_service.get_booking(db, user, data.booking_id, for_update=True)
    if booking.user_id != user.id:
        # Admins can view any booking but only the patient pays for it.
        raise NotFoundError(f"Booking {data.booking_id} not found", code="BOOKING_NOT_FOUND")

    if idempotency_key and (existing := _find_by_idempotency_key(db, user, idempotency_key)):
        db.rollback()
        return _replay(existing, data.booking_id)

    if booking.status == BookingStatus.CONFIRMED:
        raise ConflictError("Booking is already paid", code="BOOKING_ALREADY_PAID")
    if booking.status == BookingStatus.CANCELLED:
        raise ConflictError("Cannot pay for a cancelled booking", code="BOOKING_CANCELLED")
    if ensure_aware(booking.appointment_at) <= utcnow():
        raise ConflictError(
            "Cannot pay for a booking whose appointment time has passed",
            code="APPOINTMENT_IN_PAST",
        )

    outcome = _simulate_gateway(data.simulate_outcome)
    payment = Payment(
        booking_id=booking.id,
        user_id=user.id,
        amount=booking.amount,  # always charge the booking's snapshotted amount
        status=PaymentStatus.PENDING,
        provider_reference=f"pay_{uuid.uuid4().hex}",
        idempotency_key=idempotency_key,
    )
    db.add(payment)
    db.flush()
    reason = SIMULATED_DECLINE_REASON if outcome == PaymentStatus.FAILED else None
    apply_payment_outcome(booking, payment, outcome, reason)
    try:
        db.commit()
    except IntegrityError:  # concurrent request with the same Idempotency-Key won
        db.rollback()
        existing = _find_by_idempotency_key(db, user, idempotency_key or "")
        if existing is None:
            raise
        return _replay(existing, data.booking_id)

    logger.info(
        "payment_processed",
        extra={"payment_id": payment.id, "booking_id": booking.id, "status": payment.status.value},
    )
    return payment, booking.status, False


def get_payment(db: Session, user: User, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or (payment.user_id != user.id and not user.is_admin):
        raise NotFoundError(f"Payment {payment_id} not found", code="PAYMENT_NOT_FOUND")
    return payment


def list_booking_payments(db: Session, user: User, booking_id: int) -> list[Payment]:
    booking_service.get_booking(db, user, booking_id)  # access check
    return list(
        db.scalars(
            select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.id)
        ).all()
    )

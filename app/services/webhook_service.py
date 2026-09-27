"""Idempotent processing of payment-provider webhooks.

Guarantees:
1. Every event is recorded once in `webhook_events`, keyed by the provider's
   unique `event_id` (DB unique constraint).
2. Processing happens while holding a row lock on that event, so concurrent
   deliveries of the same event are serialised; the second one sees PROCESSED
   and becomes a no-op ("duplicate").
3. An event whose processing failed is stored as FAILED; a redelivery (or an
   admin replay) retries it. A PROCESSED event is never applied twice.
4. Reusing an event_id with a different payload is rejected (409); the stored
   event is left untouched.
5. Payments are keyed by the provider's unique `payment_reference`, so a
   webhook can never create a duplicate payment, and payment outcome rules
   (see `apply_payment_outcome`) make out-of-order events harmless.
"""

import hashlib
import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ConflictError, NotFoundError, ValidationError
from app.db.base import utcnow
from app.models import Booking, BookingStatus, Payment, PaymentStatus, WebhookEvent, WebhookEventStatus
from app.schemas.payment import WebhookPayload
from app.services.payment_service import apply_payment_outcome

logger = logging.getLogger(__name__)

EVENT_TYPE_TO_STATUS = {
    "payment.succeeded": PaymentStatus.SUCCESS,
    "payment.failed": PaymentStatus.FAILED,
}


@dataclass
class WebhookOutcome:
    event_id: str
    duplicate: bool
    result: str | None
    booking_status: BookingStatus | None = None
    payment_status: PaymentStatus | None = None


def payload_hash(payload: WebhookPayload) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _record_event(db: Session, payload: WebhookPayload, digest: str) -> None:
    """Insert the event if unseen. Committed on its own so receipt is durable
    even if processing later fails."""
    exists = db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_id == payload.event_id))
    if exists is not None:
        return
    db.add(
        WebhookEvent(
            event_id=payload.event_id,
            payment_reference=payload.payment_reference,
            payload=payload.model_dump(mode="json"),
            payload_hash=digest,
            status=WebhookEventStatus.RECEIVED,
            attempts=0,
        )
    )
    try:
        db.commit()
    except IntegrityError:  # a concurrent delivery inserted it first; that's fine
        db.rollback()


def _lock_event(db: Session, event_id: str) -> WebhookEvent:
    return db.scalars(
        select(WebhookEvent).where(WebhookEvent.event_id == event_id).with_for_update()
    ).one()


def _apply(db: Session, payload: WebhookPayload) -> tuple[str, Booking, Payment]:
    booking = db.scalar(
        select(Booking).where(Booking.id == payload.booking_id).with_for_update(of=Booking)
    )
    if booking is None:
        raise NotFoundError(f"Booking {payload.booking_id} not found", code="BOOKING_NOT_FOUND")
    if payload.amount != booking.amount:
        raise ValidationError(
            "Webhook amount does not match the booking amount",
            code="AMOUNT_MISMATCH",
            details={"expected": str(booking.amount), "received": str(payload.amount)},
        )

    payment = db.scalar(
        select(Payment)
        .where(Payment.provider_reference == payload.payment_reference)
        .with_for_update()
    )
    if payment is None:
        # First time we hear of this payment (e.g. the provider notified us
        # before our own request completed). The unique provider_reference
        # prevents a duplicate row even under concurrency.
        payment = Payment(
            booking_id=booking.id,
            user_id=booking.user_id,
            amount=payload.amount,
            status=PaymentStatus.PENDING,
            provider_reference=payload.payment_reference,
        )
        db.add(payment)
        db.flush()
    elif payment.booking_id != booking.id:
        raise ConflictError(
            "payment_reference belongs to a different booking", code="PAYMENT_BOOKING_MISMATCH"
        )

    result = apply_payment_outcome(
        booking, payment, EVENT_TYPE_TO_STATUS[payload.event_type], payload.failure_reason
    )
    return result, booking, payment


def process_webhook(db: Session, payload: WebhookPayload) -> WebhookOutcome:
    digest = payload_hash(payload)
    _record_event(db, payload, digest)

    event = _lock_event(db, payload.event_id)
    if event.payload_hash != digest:
        db.rollback()
        raise ConflictError(
            "event_id was already received with a different payload", code="EVENT_ID_CONFLICT"
        )
    if event.status == WebhookEventStatus.PROCESSED:
        db.rollback()
        logger.info("webhook_duplicate_ignored", extra={"event_id": payload.event_id})
        # Report current state so the ack is informative, without changing anything.
        payment = db.scalar(
            select(Payment).where(Payment.provider_reference == payload.payment_reference)
        )
        return WebhookOutcome(
            payload.event_id,
            duplicate=True,
            result=event.result,
            booking_status=payment.booking.status if payment else None,
            payment_status=payment.status if payment else None,
        )

    attempt = event.attempts + 1
    try:
        result, booking, payment = _apply(db, payload)
    except Exception as exc:
        db.rollback()  # discard partial changes, then durably record the failure
        event = _lock_event(db, payload.event_id)
        event.status = WebhookEventStatus.FAILED
        event.attempts = attempt
        event.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        db.commit()
        log = logger.warning if isinstance(exc, AppError) else logger.exception
        log("webhook_processing_failed", extra={"event_id": payload.event_id, "attempt": attempt})
        raise

    event.status = WebhookEventStatus.PROCESSED
    event.result = result
    event.attempts = attempt
    event.last_error = None
    event.processed_at = utcnow()
    db.commit()
    logger.info(
        "webhook_processed",
        extra={"event_id": payload.event_id, "result": result, "booking_id": booking.id},
    )
    return WebhookOutcome(
        payload.event_id,
        duplicate=False,
        result=result,
        booking_status=booking.status,
        payment_status=payment.status,
    )


def replay_event(db: Session, event_id: str) -> WebhookOutcome:
    """Admin-triggered retry of a stored event (e.g. after fixing bad data)."""
    event = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
    if event is None:
        raise NotFoundError(f"Webhook event {event_id} not found", code="EVENT_NOT_FOUND")
    return process_webhook(db, WebhookPayload.model_validate(event.payload))

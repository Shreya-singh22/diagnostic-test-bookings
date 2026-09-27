from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import ValidationError as PydanticValidationError
from starlette.concurrency import run_in_threadpool

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.exceptions import UnauthorizedError, ValidationError
from app.core.rate_limit import rate_limit
from app.core.security import verify_webhook_signature
from app.schemas.payment import PaymentCreate, PaymentOut, PaymentResult, WebhookAck, WebhookPayload
from app.services import payment_service, webhook_service
from app.services.webhook_service import WebhookOutcome

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/",
    response_model=PaymentResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("payments", "payment_rate_limit"))],
    responses={
        200: {"description": "Replay of an earlier request with the same Idempotency-Key"},
        404: {"description": "Booking not found (or not yours)"},
        409: {"description": "Booking already paid, cancelled or expired; key reused"},
    },
)
def create_payment(
    data: PaymentCreate,
    db: DbSession,
    user: CurrentUser,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            max_length=100,
            description="Optional. Retrying with the same key returns the original payment "
            "instead of charging again.",
        ),
    ] = None,
) -> PaymentResult:
    """Simulate charging a booking. The result is SUCCESS or FAILED and the
    booking moves to CONFIRMED or FAILED accordingly. A FAILED booking can be
    paid again."""
    payment, booking_status, replayed = payment_service.create_payment(
        db, user, data, idempotency_key
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return PaymentResult(payment=PaymentOut.model_validate(payment), booking_status=booking_status)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, db: DbSession, user: CurrentUser) -> PaymentOut:
    return payment_service.get_payment(db, user, payment_id)


def _ack(outcome: WebhookOutcome) -> WebhookAck:
    return WebhookAck(
        event_id=outcome.event_id,
        status="duplicate" if outcome.duplicate else "processed",
        result=outcome.result,
        booking_status=outcome.booking_status,
        payment_status=outcome.payment_status,
    )


@router.post(
    "/webhook/",
    response_model=WebhookAck,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": WebhookPayload.model_json_schema()}},
        },
        "parameters": [
            {
                "name": "X-Webhook-Signature",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "hex(HMAC-SHA256(WEBHOOK_SECRET, raw request body))",
            }
        ],
    },
    responses={
        401: {"description": "Missing or invalid signature"},
        409: {"description": "event_id reused with a different payload, or reference mismatch"},
    },
)
async def payment_webhook(request: Request, db: DbSession) -> WebhookAck:
    """Receive a payment-status event from the payment provider.

    Idempotent: redelivering an already-processed event returns
    `status: "duplicate"` and changes nothing. An event that failed
    processing is retried when it is redelivered.
    """
    body = await request.body()
    # Authenticate on the exact raw bytes, before parsing anything.
    if not verify_webhook_signature(body, request.headers.get("X-Webhook-Signature")):
        raise UnauthorizedError("Invalid webhook signature", code="INVALID_SIGNATURE")
    try:
        payload = WebhookPayload.model_validate_json(body)
    except PydanticValidationError as exc:
        details = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        raise ValidationError("Invalid webhook payload", details=details)
    # DB work is blocking; keep it off the event loop.
    return _ack(await run_in_threadpool(webhook_service.process_webhook, db, payload))


@router.post("/webhook/events/{event_id}/replay", response_model=WebhookAck)
def replay_webhook_event(event_id: str, db: DbSession, _: AdminUser) -> WebhookAck:
    """Admin: re-run processing for a stored event (no-op if already processed)."""
    return _ack(webhook_service.replay_event(db, event_id))

import json

from fastapi.testclient import TestClient

from app.core.security import sign_webhook_payload
from app.main import app
from app.models import Payment, WebhookEvent, WebhookEventStatus
from app.services import webhook_service


def _event(booking, reference="pay_ext_1", event_type="payment.succeeded", event_id="evt_1", **extra):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "payment_reference": reference,
        "booking_id": booking["id"],
        "amount": booking["amount"],
        **extra,
    }


def _send(client, payload, signature=None):
    body = json.dumps(payload).encode()
    sig = signature if signature is not None else sign_webhook_payload(body)
    return client.post(
        "/payments/webhook/",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
    )


def _status(client, headers, booking):
    return client.get(f"/bookings/{booking['id']}", headers=headers).json()["status"]


# ----- Authentication & validation ----------------------------------------


def test_rejects_missing_or_invalid_signature(client, booking, db):
    payload = _event(booking)
    assert _send(client, payload, signature="").status_code == 401
    resp = _send(client, payload, signature="deadbeef")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert db.query(WebhookEvent).count() == 0


def test_rejects_malformed_payload(client, booking):
    assert _send(client, {"event_id": "x"}).status_code == 422
    assert _send(client, _event(booking, event_type="payment.refunded")).status_code == 422
    body = b"{not json"
    resp = client.post(
        "/payments/webhook/", content=body, headers={"X-Webhook-Signature": sign_webhook_payload(body)}
    )
    assert resp.status_code == 422


# ----- Core behaviour ------------------------------------------------------


def test_success_event_confirms_booking_and_records_payment(client, user_headers, booking, db):
    resp = _send(client, _event(booking))
    assert resp.status_code == 200
    assert resp.json() == {
        "event_id": "evt_1",
        "status": "processed",
        "result": "applied",
        "booking_status": "CONFIRMED",
        "payment_status": "SUCCESS",
    }
    assert _status(client, user_headers, booking) == "CONFIRMED"
    payment = db.query(Payment).one()
    assert payment.provider_reference == "pay_ext_1" and payment.booking_id == booking["id"]


def test_webhook_updates_payment_created_by_payments_api(client, user_headers, booking, db):
    paid = client.post(
        "/payments/", json={"booking_id": booking["id"], "simulate_outcome": "FAILED"}, headers=user_headers
    ).json()
    reference = paid["payment"]["provider_reference"]
    resp = _send(client, _event(booking, reference=reference))
    assert resp.json()["result"] == "applied"
    assert _status(client, user_headers, booking) == "CONFIRMED"
    assert db.query(Payment).count() == 1


def test_failed_event_marks_booking_failed(client, user_headers, booking):
    resp = _send(client, _event(booking, event_type="payment.failed", failure_reason="Insufficient funds"))
    assert resp.json()["booking_status"] == "FAILED"
    payment = client.get(f"/bookings/{booking['id']}/payments", headers=user_headers).json()[0]
    assert payment["failure_reason"] == "Insufficient funds"


# ----- Idempotency ---------------------------------------------------------


def test_same_event_delivered_many_times_is_applied_once(client, user_headers, booking, db):
    payload = _event(booking)
    responses = [_send(client, payload) for _ in range(5)]
    assert [r.status_code for r in responses] == [200] * 5
    assert [r.json()["status"] for r in responses] == ["processed"] + ["duplicate"] * 4
    assert responses[-1].json()["booking_status"] == "CONFIRMED"
    assert db.query(Payment).count() == 1
    event = db.query(WebhookEvent).one()
    assert event.status == WebhookEventStatus.PROCESSED and event.attempts == 1
    assert _status(client, user_headers, booking) == "CONFIRMED"


def test_distinct_events_for_same_payment_do_not_duplicate_payment(client, booking, db):
    _send(client, _event(booking, event_id="evt_a"))
    resp = _send(client, _event(booking, event_id="evt_b"))
    assert resp.json()["result"] == "no_change"
    assert db.query(Payment).count() == 1
    assert db.query(WebhookEvent).count() == 2


def test_reused_event_id_with_different_payload_is_rejected(client, user_headers, booking):
    _send(client, _event(booking, event_type="payment.failed"))
    resp = _send(client, _event(booking, event_type="payment.succeeded"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EVENT_ID_CONFLICT"
    assert _status(client, user_headers, booking) == "FAILED"


def test_out_of_order_failure_after_success_is_ignored(client, user_headers, booking):
    _send(client, _event(booking, event_id="evt_ok"))
    late = _send(client, _event(booking, event_id="evt_late", event_type="payment.failed"))
    assert late.status_code == 200
    assert late.json()["result"] == "ignored_stale"
    assert late.json()["payment_status"] == "SUCCESS"
    assert _status(client, user_headers, booking) == "CONFIRMED"


def test_success_for_cancelled_booking_does_not_resurrect_it(client, user_headers, booking):
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    resp = _send(client, _event(booking))
    assert resp.json()["result"] == "applied_refund_required"
    assert _status(client, user_headers, booking) == "CANCELLED"


def test_failed_payment_does_not_unconfirm_booking_paid_by_another_payment(client, user_headers, booking):
    _send(client, _event(booking, reference="pay_A", event_id="e1"))
    resp = _send(client, _event(booking, reference="pay_B", event_id="e2", event_type="payment.failed"))
    assert resp.json()["payment_status"] == "FAILED"
    assert _status(client, user_headers, booking) == "CONFIRMED"


# ----- Invalid references --------------------------------------------------


def test_unknown_booking_is_rejected_and_recorded(client, booking, db):
    resp = _send(client, {**_event(booking), "booking_id": 999999})
    assert resp.status_code == 404
    event = db.query(WebhookEvent).one()
    assert event.status == WebhookEventStatus.FAILED
    assert "not found" in event.last_error
    assert db.query(Payment).count() == 0


def test_amount_mismatch_is_rejected(client, user_headers, booking):
    resp = _send(client, _event(booking, amount="1.00"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "AMOUNT_MISMATCH"
    assert _status(client, user_headers, booking) == "PENDING"


def test_reference_for_other_booking_is_rejected(client, user_headers, booking, catalog):
    from tests.conftest import future

    second = client.post(
        "/bookings",
        json={"centre_id": catalog["centre_id"], "test_id": catalog["test_id"], "appointment_at": future(8)},
        headers=user_headers,
    ).json()
    _send(client, _event(booking, reference="pay_X", event_id="e1"))
    resp = _send(client, _event(second, reference="pay_X", event_id="e2"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PAYMENT_BOOKING_MISMATCH"
    assert _status(client, user_headers, second) == "PENDING"


# ----- Retry handling ------------------------------------------------------


def test_transient_failure_is_retried_on_redelivery(client, user_headers, booking, db, monkeypatch):
    real_apply = webhook_service._apply
    calls = {"n": 0}

    def flaky_apply(session, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return real_apply(session, payload)

    monkeypatch.setattr(webhook_service, "_apply", flaky_apply)
    payload = _event(booking)
    with TestClient(app, raise_server_exceptions=False) as c:
        first = _send(c, payload)
    assert first.status_code == 500
    assert db.query(Payment).count() == 0  # partial work was rolled back
    event = db.query(WebhookEvent).one()
    assert event.status == WebhookEventStatus.FAILED and event.attempts == 1

    second = _send(client, payload)  # provider redelivers
    assert second.json()["status"] == "processed"
    db.expire_all()
    event = db.query(WebhookEvent).one()
    assert event.status == WebhookEventStatus.PROCESSED and event.attempts == 2
    assert event.last_error is None
    assert _status(client, user_headers, booking) == "CONFIRMED"


def test_admin_can_replay_a_failed_event(client, admin_headers, user_headers, booking, monkeypatch):
    def broken_apply(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(webhook_service, "_apply", broken_apply)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert _send(c, _event(booking)).status_code == 500
    monkeypatch.undo()

    assert client.post("/payments/webhook/events/evt_1/replay", headers=user_headers).status_code == 403
    resp = client.post("/payments/webhook/events/evt_1/replay", headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["status"] == "processed"
    again = client.post("/payments/webhook/events/evt_1/replay", headers=admin_headers)
    assert again.json()["status"] == "duplicate"
    assert client.post("/payments/webhook/events/nope/replay", headers=admin_headers).status_code == 404


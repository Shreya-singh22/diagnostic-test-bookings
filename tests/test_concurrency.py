"""Race-condition tests. They need real row locks, so they run on PostgreSQL only
(set TEST_DATABASE_URL); SQLite serialises writers and would prove nothing."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.core.security import sign_webhook_payload
from app.db.session import engine
from app.main import app
from app.models import Payment, WebhookEvent

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql", reason="requires PostgreSQL row locking"
)

WORKERS = 8


def _race(fn):
    """Run `fn(client)` in WORKERS threads released at the same instant."""
    barrier = threading.Barrier(WORKERS)

    def run(_):
        client = TestClient(app)
        barrier.wait()
        return fn(client)

    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(run, range(WORKERS)))


def test_concurrent_duplicate_webhooks_apply_exactly_once(booking, db):
    body = json.dumps(
        {
            "event_id": "evt_race",
            "event_type": "payment.succeeded",
            "payment_reference": "pay_race",
            "booking_id": booking["id"],
            "amount": booking["amount"],
        }
    ).encode()
    headers = {"Content-Type": "application/json", "X-Webhook-Signature": sign_webhook_payload(body)}

    responses = _race(lambda c: c.post("/payments/webhook/", content=body, headers=headers))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    statuses = sorted(r.json()["status"] for r in responses)
    assert statuses == ["duplicate"] * (WORKERS - 1) + ["processed"]
    assert db.query(Payment).count() == 1
    assert db.query(WebhookEvent).one().attempts == 1


def test_concurrent_payments_charge_a_booking_once(booking, user_headers, db):
    body = {"booking_id": booking["id"], "simulate_outcome": "SUCCESS"}

    responses = _race(lambda c: c.post("/payments/", json=body, headers=user_headers))

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * (WORKERS - 1), [r.text for r in responses]
    assert db.query(Payment).count() == 1


def test_concurrent_retries_with_same_idempotency_key(booking, user_headers, db):
    body = {"booking_id": booking["id"], "simulate_outcome": "SUCCESS"}
    headers = {**user_headers, "Idempotency-Key": "same-key"}

    responses = _race(lambda c: c.post("/payments/", json=body, headers=headers))

    assert sorted(r.status_code for r in responses) == [200] * (WORKERS - 1) + [201]
    assert len({r.json()["payment"]["id"] for r in responses}) == 1
    assert db.query(Payment).count() == 1

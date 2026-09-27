from app.models import Payment


def _pay(client, headers, booking_id, outcome="SUCCESS", key=None):
    extra = {"Idempotency-Key": key} if key else {}
    return client.post(
        "/payments/",
        json={"booking_id": booking_id, "simulate_outcome": outcome},
        headers={**headers, **extra},
    )


def _booking_status(client, headers, booking_id):
    return client.get(f"/bookings/{booking_id}", headers=headers).json()["status"]


def test_successful_payment_confirms_booking(client, user_headers, booking):
    resp = _pay(client, user_headers, booking["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["payment"]["status"] == "SUCCESS"
    assert body["payment"]["amount"] == "500.00"
    assert body["payment"]["provider_reference"].startswith("pay_")
    assert body["booking_status"] == "CONFIRMED"
    assert _booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_failed_payment_marks_booking_failed_and_can_be_retried(client, user_headers, booking):
    failed = _pay(client, user_headers, booking["id"], "FAILED").json()
    assert failed["payment"]["status"] == "FAILED"
    assert failed["payment"]["failure_reason"]
    assert failed["booking_status"] == "FAILED"

    retry = _pay(client, user_headers, booking["id"], "SUCCESS").json()
    assert retry["booking_status"] == "CONFIRMED"
    payments = client.get(f"/bookings/{booking['id']}/payments", headers=user_headers).json()
    assert [p["status"] for p in payments] == ["FAILED", "SUCCESS"]


def test_cannot_pay_twice(client, user_headers, booking):
    _pay(client, user_headers, booking["id"])
    resp = _pay(client, user_headers, booking["id"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BOOKING_ALREADY_PAID"


def test_cannot_pay_cancelled_booking(client, user_headers, booking):
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    resp = _pay(client, user_headers, booking["id"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BOOKING_CANCELLED"


def test_cannot_pay_unknown_or_someone_elses_booking(client, user_headers, other_headers, admin_headers, booking):
    assert _pay(client, user_headers, 999999).status_code == 404
    assert _pay(client, other_headers, booking["id"]).status_code == 404
    assert _pay(client, admin_headers, booking["id"]).status_code == 404  # admins don't pay for patients
    assert _booking_status(client, user_headers, booking["id"]) == "PENDING"


def test_payment_requires_auth_and_valid_body(client, user_headers, booking):
    assert client.post("/payments/", json={"booking_id": booking["id"]}).status_code == 401
    bad = client.post(
        "/payments/", json={"booking_id": booking["id"], "simulate_outcome": "MAYBE"}, headers=user_headers
    )
    assert bad.status_code == 422
    assert client.post("/payments/", json={}, headers=user_headers).status_code == 422


def test_idempotency_key_replays_original_payment(client, user_headers, booking, db):
    first = _pay(client, user_headers, booking["id"], key="order-123")
    second = _pay(client, user_headers, booking["id"], key="order-123")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["payment"]["id"] == first.json()["payment"]["id"]
    assert db.query(Payment).count() == 1


def test_idempotency_key_reuse_for_other_booking_is_rejected(client, user_headers, booking, catalog):
    from tests.conftest import future

    other = client.post(
        "/bookings",
        json={"centre_id": catalog["centre_id"], "test_id": catalog["test_id"], "appointment_at": future(9)},
        headers=user_headers,
    ).json()
    _pay(client, user_headers, booking["id"], key="k1")
    resp = _pay(client, user_headers, other["id"], key="k1")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_random_outcome_uses_configured_success_rate(client, user_headers, booking, settings, monkeypatch):
    monkeypatch.setattr(settings, "payment_success_rate", 0.0)
    resp = client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    assert resp.json()["payment"]["status"] == "FAILED"


def test_get_payment_is_owner_only(client, user_headers, other_headers, booking):
    payment_id = _pay(client, user_headers, booking["id"]).json()["payment"]["id"]
    assert client.get(f"/payments/{payment_id}", headers=user_headers).status_code == 200
    assert client.get(f"/payments/{payment_id}", headers=other_headers).status_code == 404

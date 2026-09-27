from datetime import datetime, timedelta, timezone

import pytest

from app.models import BookingStatus
from app.services.booking_service import ALLOWED_TRANSITIONS
from tests.conftest import future


def _book(client, headers, catalog, **overrides):
    body = {"centre_id": catalog["centre_id"], "test_id": catalog["test_id"], "appointment_at": future()}
    body.update(overrides)
    return client.post("/bookings", json=body, headers=headers)


def test_create_booking_uses_server_side_price(client, user_headers, catalog):
    resp = _book(client, user_headers, catalog, amount="1.00")  # client amount is ignored
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["amount"] == "500.00"
    assert body["centre"]["id"] == catalog["centre_id"]
    assert body["test"]["code"] == "CBC"


def test_booking_requires_auth(client, catalog):
    assert _book(client, {}, catalog).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"appointment_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
        {"appointment_at": "not-a-date"},
        {"centre_id": 0},
        {"test_id": "abc"},
    ],
)
def test_invalid_booking_requests(client, user_headers, catalog, overrides):
    resp = _book(client, user_headers, catalog, **overrides)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_centre_and_unoffered_test(client, user_headers, catalog):
    resp = _book(client, user_headers, catalog, centre_id=9999)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CENTRE_NOT_FOUND"
    resp = _book(client, user_headers, catalog, test_id=catalog["unoffered_test_id"])
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "TEST_NOT_OFFERED"


def test_duplicate_active_booking_for_same_slot(client, user_headers, catalog):
    slot = future()
    first = _book(client, user_headers, catalog, appointment_at=slot)
    # Same instant expressed in another timezone is still a duplicate.
    ist = datetime.fromisoformat(slot).astimezone(timezone(timedelta(hours=5, minutes=30)))
    dup = _book(client, user_headers, catalog, appointment_at=ist.isoformat())
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "DUPLICATE_BOOKING"

    client.post(f"/bookings/{first.json()['id']}/cancel", headers=user_headers)
    assert _book(client, user_headers, catalog, appointment_at=slot).status_code == 201


def test_price_change_does_not_affect_existing_booking(client, admin_headers, user_headers, booking, catalog):
    client.patch(
        f"/centres/{catalog['centre_id']}/tests/{catalog['test_id']}",
        json={"price": "999.00"},
        headers=admin_headers,
    )
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["amount"] == "500.00"
    assert _book(client, user_headers, catalog, appointment_at=future(5)).json()["amount"] == "999.00"


def test_users_cannot_see_or_modify_others_bookings(client, other_headers, admin_headers, booking):
    url = f"/bookings/{booking['id']}"
    assert client.get(url, headers=other_headers).status_code == 404
    resp = client.post(f"{url}/cancel", headers=other_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "BOOKING_NOT_FOUND"
    assert client.get(f"{url}/payments", headers=other_headers).status_code == 404
    assert client.get(url, headers=admin_headers).status_code == 200


def test_list_bookings_is_scoped_and_filterable(client, user_headers, other_headers, catalog, booking):
    _book(client, other_headers, catalog)
    mine = client.get("/bookings", headers=user_headers).json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == booking["id"]
    assert client.get("/bookings", params={"status": "CONFIRMED"}, headers=user_headers).json()["total"] == 0
    assert client.get("/bookings", params={"status": "BOGUS"}, headers=user_headers).status_code == 422


def test_cancel_booking(client, user_headers, booking):
    url = f"/bookings/{booking['id']}/cancel"
    resp = client.post(url, headers=user_headers)
    assert resp.status_code == 200 and resp.json()["status"] == "CANCELLED"
    again = client.post(url, headers=user_headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "BOOKING_ALREADY_CANCELLED"


def test_cancel_unknown_booking(client, user_headers):
    assert client.post("/bookings/424242/cancel", headers=user_headers).status_code == 404


def test_state_machine_has_no_way_out_of_cancelled():
    assert ALLOWED_TRANSITIONS[BookingStatus.CANCELLED] == set()
    assert BookingStatus.PENDING not in ALLOWED_TRANSITIONS[BookingStatus.CONFIRMED]
    assert BookingStatus.FAILED not in ALLOWED_TRANSITIONS[BookingStatus.CONFIRMED]

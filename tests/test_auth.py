from datetime import datetime, timedelta, timezone

import jwt

from tests.conftest import PASSWORD, signup_and_login


def test_signup_returns_user_without_password_and_normalises_email(client):
    resp = client.post(
        "/auth/signup",
        json={"email": "  Priya@Example.COM ", "password": PASSWORD, "full_name": "  Priya  "},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "priya@example.com"
    assert body["full_name"] == "Priya"
    assert body["role"] == "USER"
    assert "password" not in body and "hashed_password" not in body


def test_signup_cannot_escalate_role(client):
    resp = client.post(
        "/auth/signup",
        json={"email": "a@example.com", "password": PASSWORD, "full_name": "A", "role": "ADMIN"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "USER"


def test_duplicate_email_is_rejected_case_insensitively(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": PASSWORD, "full_name": "A"})
    resp = client.post(
        "/auth/signup", json={"email": "A@EXAMPLE.com", "password": PASSWORD, "full_name": "A"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_signup_validation_errors_use_consistent_format(client):
    resp = client.post(
        "/auth/signup", json={"email": "not-an-email", "password": "short", "full_name": " "}
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    bad_fields = {d["loc"][-1] for d in error["details"]}
    assert bad_fields == {"email", "password", "full_name"}


def test_signup_rejects_weak_password(client):
    resp = client.post(
        "/auth/signup", json={"email": "a@example.com", "password": "onlyletters", "full_name": "A"}
    )
    assert resp.status_code == 422


def test_login_success_and_me(client):
    headers = signup_and_login(client, "me@example.com")
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


def test_login_failure_does_not_reveal_which_part_was_wrong(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": PASSWORD, "full_name": "A"})
    wrong_password = client.post("/auth/login", json={"email": "a@example.com", "password": "Wrong123"})
    unknown_user = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_protected_endpoint_requires_valid_token(client, settings):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401

    expired = jwt.encode(
        {"sub": "1", "type": "access", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"

    forged = jwt.encode(
        {"sub": "1", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "some-other-secret-that-is-long-enough-32b",
        algorithm="HS256",
    )
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_token_for_deleted_user_is_rejected(client, db):
    from app.models import User

    headers = signup_and_login(client, "gone@example.com")
    user = db.query(User).filter_by(email="gone@example.com").one()
    user.is_active = False
    db.commit()
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_login_is_rate_limited(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit", 3)
    body = {"email": "x@example.com", "password": "Whatever1"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]

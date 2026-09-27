"""Test fixtures.

Runs against PostgreSQL when TEST_DATABASE_URL is set (recommended, e.g.
postgresql+psycopg://eve:eve@localhost:5432/eve_test), otherwise against a
throwaway SQLite file so `pytest` works with zero setup.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

# Configure before the app (and its engine) is imported.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db"
)
os.environ["REDIS_URL"] = ""
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-long-enough-for-hs256"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["BCRYPT_ROUNDS"] = "4"  # fast hashing in tests

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import update  # noqa: E402

from app.core.cache import InMemoryCache, set_cache  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User, UserRole  # noqa: E402

PASSWORD = "Passw0rd!"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_state():
    """Empty every table and the cache before each test."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    set_cache(InMemoryCache())
    yield


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def signup_and_login(client: TestClient, email: str, *, admin: bool = False) -> dict:
    resp = client.post(
        "/auth/signup", json={"email": email, "password": PASSWORD, "full_name": email.split("@")[0]}
    )
    assert resp.status_code == 201, resp.text
    if admin:
        with SessionLocal() as s:
            s.execute(update(User).where(User.email == email).values(role=UserRole.ADMIN))
            s.commit()
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


@pytest.fixture
def user_headers(client):
    return signup_and_login(client, "patient@example.com")


@pytest.fixture
def other_headers(client):
    return signup_and_login(client, "someone.else@example.com")


@pytest.fixture
def admin_headers(client):
    return signup_and_login(client, "admin@example.com", admin=True)


@pytest.fixture
def catalog(client, admin_headers):
    """One centre offering one test at 500.00, plus a test it doesn't offer."""
    cbc = client.post(
        "/tests", json={"code": "cbc", "name": "Complete Blood Count"}, headers=admin_headers
    ).json()
    tsh = client.post("/tests", json={"code": "TSH", "name": "Thyroid"}, headers=admin_headers).json()
    centre = client.post(
        "/centres",
        json={"name": "EVE Indiranagar", "address": "100 Feet Rd", "city": "Bengaluru", "pincode": "560038"},
        headers=admin_headers,
    ).json()
    resp = client.post(
        f"/centres/{centre['id']}/tests",
        json={"test_id": cbc["id"], "price": "500.00"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return {"centre_id": centre["id"], "test_id": cbc["id"], "unoffered_test_id": tsh["id"]}


def future(days: int = 3) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


@pytest.fixture
def booking(client, user_headers, catalog):
    resp = client.post(
        "/bookings",
        json={"centre_id": catalog["centre_id"], "test_id": catalog["test_id"], "appointment_at": future()},
        headers=user_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()

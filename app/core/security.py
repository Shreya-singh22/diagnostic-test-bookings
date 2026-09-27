import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:  # malformed hash or password > 72 bytes
        return False


def create_access_token(user_id: int) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_in = settings.jwt_expire_minutes * 60
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def decode_access_token(token: str) -> int | None:
    """Return the user id, or None if the token is invalid/expired."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        return int(payload["sub"])
    except (TypeError, ValueError):
        return None


def sign_webhook_payload(body: bytes, secret: str | None = None) -> str:
    """HMAC-SHA256 hex digest the provider sends in `X-Webhook-Signature`."""
    key = (secret or get_settings().webhook_secret).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_webhook_payload(body), signature)

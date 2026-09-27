import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.models import User, UserRole
from app.schemas.auth import LoginRequest, SignupRequest

logger = logging.getLogger(__name__)

# Compared against when the email is unknown, so the response time doesn't
# reveal whether an account exists.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def signup(db: Session, data: SignupRequest) -> User:
    if db.scalar(select(User.id).where(User.email == data.email)) is not None:
        raise ConflictError("Email is already registered", code="EMAIL_ALREADY_REGISTERED")
    # Public signup always creates a regular user; admins are provisioned out of band.
    user = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role=UserRole.USER,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # concurrent signup with the same email
        db.rollback()
        raise ConflictError("Email is already registered", code="EMAIL_ALREADY_REGISTERED")
    logger.info("user_signed_up", extra={"user_id": user.id})
    return user


def authenticate(db: Session, data: LoginRequest) -> User:
    user = db.scalar(select(User).where(User.email == data.email))
    hashed = user.hashed_password if user else _DUMMY_HASH
    if not verify_password(data.password, hashed) or user is None or not user.is_active:
        logger.info("login_failed")
        raise UnauthorizedError("Invalid email or password", code="INVALID_CREDENTIALS")
    return user

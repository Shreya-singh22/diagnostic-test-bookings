"""Import all models so SQLAlchemy metadata (and Alembic) sees every table."""

from app.models.booking import Booking
from app.models.centre import CentreTest, DiagnosticCentre, DiagnosticTest
from app.models.enums import BookingStatus, PaymentStatus, UserRole, WebhookEventStatus
from app.models.payment import Payment, WebhookEvent
from app.models.user import User

__all__ = [
    "Booking",
    "BookingStatus",
    "CentreTest",
    "DiagnosticCentre",
    "DiagnosticTest",
    "Payment",
    "PaymentStatus",
    "User",
    "UserRole",
    "WebhookEvent",
    "WebhookEventStatus",
]

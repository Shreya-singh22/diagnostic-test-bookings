import enum


class UserRole(str, enum.Enum):
    USER = "USER"
    ADMIN = "ADMIN"


class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"  # created, awaiting payment
    CONFIRMED = "CONFIRMED"  # paid
    FAILED = "FAILED"  # last payment attempt failed (can be retried)
    CANCELLED = "CANCELLED"  # terminal


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class WebhookEventStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"  # processing error; a redelivery of the same event is retried

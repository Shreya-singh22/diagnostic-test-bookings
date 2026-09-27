from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.centre import DiagnosticCentre, DiagnosticTest
from app.models.enums import BookingStatus
from app.models.user import User

_ACTIVE_STATUSES = "status IN ('PENDING', 'CONFIRMED')"


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # A booking can only reference a test the centre actually offers.
        ForeignKeyConstraint(
            ["centre_id", "test_id"],
            ["centre_tests.centre_id", "centre_tests.test_id"],
            name="fk_bookings_centre_test",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        # A user cannot hold two live bookings for the same test/centre/slot.
        Index(
            "uq_bookings_active_slot",
            "user_id",
            "centre_id",
            "test_id",
            "appointment_at",
            unique=True,
            postgresql_where=text(_ACTIVE_STATUSES),
            sqlite_where=text(_ACTIVE_STATUSES),
        ),
        Index("ix_bookings_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id"))
    test_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tests.id"))
    appointment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Price snapshot at booking time; later price changes don't affect it.
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=20, create_constraint=True),
        default=BookingStatus.PENDING,
        index=True,
    )

    user: Mapped[User] = relationship()
    centre: Mapped[DiagnosticCentre] = relationship(lazy="joined", innerjoin=True)
    test: Mapped[DiagnosticTest] = relationship(lazy="joined", innerjoin=True)

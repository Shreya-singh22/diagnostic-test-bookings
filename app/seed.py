"""Seed an admin user and sample centres/tests. Safe to run repeatedly.

    python -m app.seed
"""

import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User, UserRole

TESTS = [
    ("CBC", "Complete Blood Count", "Measures red cells, white cells and platelets."),
    ("LIPID", "Lipid Profile", "Cholesterol and triglycerides."),
    ("TSH", "Thyroid Stimulating Hormone", "Screens thyroid function."),
    ("HBA1C", "HbA1c", "Average blood sugar over ~3 months."),
    ("VITD", "Vitamin D (25-OH)", None),
]

CENTRES = [
    (
        ("EVE Diagnostics Indiranagar", "12, 100 Feet Rd, Indiranagar", "Bengaluru", "560038"),
        {"CBC": "350.00", "LIPID": "650.00", "TSH": "400.00", "HBA1C": "550.00"},
    ),
    (
        ("EVE Diagnostics Koramangala", "80 Feet Rd, 4th Block, Koramangala", "Bengaluru", "560034"),
        {"CBC": "320.00", "VITD": "1200.00"},
    ),
    (
        ("EVE Diagnostics Bandra", "Hill Road, Bandra West", "Mumbai", "400050"),
        {"CBC": "400.00", "LIPID": "700.00", "VITD": "1350.00"},
    ),
]


def seed(db: Session) -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "admin@eve.local").lower()
    if db.scalar(select(User).where(User.email == admin_email)) is None:
        db.add(
            User(
                email=admin_email,
                full_name="EVE Admin",
                hashed_password=hash_password(os.getenv("ADMIN_PASSWORD", "Admin@12345")),
                role=UserRole.ADMIN,
            )
        )

    tests: dict[str, DiagnosticTest] = {}
    for code, name, description in TESTS:
        test = db.scalar(select(DiagnosticTest).where(DiagnosticTest.code == code))
        if test is None:
            test = DiagnosticTest(code=code, name=name, description=description)
            db.add(test)
        tests[code] = test
    db.flush()

    for (name, address, city, pincode), prices in CENTRES:
        centre = db.scalar(
            select(DiagnosticCentre).where(
                DiagnosticCentre.name == name, DiagnosticCentre.address == address
            )
        )
        if centre is None:
            centre = DiagnosticCentre(name=name, address=address, city=city, pincode=pincode)
            db.add(centre)
            db.flush()
        offered = {o.test_id for o in centre.offerings}
        for code, price in prices.items():
            if tests[code].id not in offered:
                db.add(CentreTest(centre_id=centre.id, test_id=tests[code].id, price=Decimal(price)))
    db.commit()


if __name__ == "__main__":
    with SessionLocal() as session:
        seed(session)
    print("Seed complete. Admin:", os.getenv("ADMIN_EMAIL", "admin@eve.local"))

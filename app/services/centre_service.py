"""Diagnostic centres, the test catalogue and per-centre test pricing.

Public reads are cached (namespace "centres"); every admin write bumps the
namespace so stale data is never served after a change.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest
from app.schemas.centre import (
    CentreCreate,
    CentreDetailOut,
    CentreOut,
    CentreTestCreate,
    CentreTestOut,
    CentreTestUpdate,
    CentreUpdate,
    DiagnosticTestCreate,
    DiagnosticTestOut,
)
from app.schemas.common import Page, PageParams

CACHE_NAMESPACE = "centres"


def _cached(key_parts: dict[str, Any], producer: Callable[[], dict]) -> dict:
    cache = get_cache()
    version = cache.namespace_version(CACHE_NAMESPACE)
    digest = hashlib.sha256(json.dumps(key_parts, sort_keys=True).encode()).hexdigest()[:24]
    key = f"{CACHE_NAMESPACE}:v{version}:{digest}"
    if (hit := cache.get(key)) is not None:
        return hit
    value = producer()
    cache.set(key, value, get_settings().cache_ttl_seconds)
    return value


def _invalidate() -> None:
    get_cache().bump_namespace(CACHE_NAMESPACE)


def _commit_or_conflict(db: Session, message: str, code: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(message, code=code)
    _invalidate()


# ----- Test catalogue ------------------------------------------------------


def list_tests(db: Session, page: PageParams, search: str | None) -> dict:
    def produce() -> dict:
        query = select(DiagnosticTest)
        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(
                func.lower(DiagnosticTest.name).like(pattern)
                | func.lower(DiagnosticTest.code).like(pattern)
            )
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(
            query.order_by(DiagnosticTest.name).limit(page.limit).offset(page.offset)
        ).all()
        return Page[DiagnosticTestOut](
            items=[DiagnosticTestOut.model_validate(r) for r in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        ).model_dump(mode="json")

    return _cached({"r": "tests", "q": search, "l": page.limit, "o": page.offset}, produce)


def create_test(db: Session, data: DiagnosticTestCreate) -> DiagnosticTest:
    test = DiagnosticTest(**data.model_dump())
    db.add(test)
    _commit_or_conflict(db, "A test with this code or name already exists", "TEST_ALREADY_EXISTS")
    return test


# ----- Centres -------------------------------------------------------------


def list_centres(db: Session, page: PageParams, city: str | None, test_id: int | None) -> dict:
    def produce() -> dict:
        query = select(DiagnosticCentre).where(DiagnosticCentre.is_active.is_(True))
        if city:
            query = query.where(func.lower(DiagnosticCentre.city) == city.strip().lower())
        if test_id:
            query = query.where(
                DiagnosticCentre.offerings.any(
                    (CentreTest.test_id == test_id) & CentreTest.is_active.is_(True)
                )
            )
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(
            query.order_by(DiagnosticCentre.name, DiagnosticCentre.id)
            .limit(page.limit)
            .offset(page.offset)
        ).all()
        return Page[CentreOut](
            items=[CentreOut.model_validate(r) for r in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        ).model_dump(mode="json")

    key = {"r": "centres", "city": city, "test": test_id, "l": page.limit, "o": page.offset}
    return _cached(key, produce)


def _get_centre(db: Session, centre_id: int) -> DiagnosticCentre:
    centre = db.get(DiagnosticCentre, centre_id)
    if centre is None:
        raise NotFoundError(f"Centre {centre_id} not found", code="CENTRE_NOT_FOUND")
    return centre


def _centre_detail(centre: DiagnosticCentre, include_inactive: bool) -> CentreDetailOut:
    offerings = [o for o in centre.offerings if include_inactive or o.is_active]
    return CentreDetailOut(
        **CentreOut.model_validate(centre).model_dump(),
        tests=[CentreTestOut.model_validate(o) for o in offerings],
    )


def get_centre_detail(db: Session, centre_id: int) -> dict:
    """Public view: inactive centres are hidden and only active tests are listed."""

    def produce() -> dict:
        centre = db.scalar(
            select(DiagnosticCentre)
            .options(selectinload(DiagnosticCentre.offerings))
            .where(DiagnosticCentre.id == centre_id, DiagnosticCentre.is_active.is_(True))
        )
        if centre is None:
            raise NotFoundError(f"Centre {centre_id} not found", code="CENTRE_NOT_FOUND")
        return _centre_detail(centre, include_inactive=False).model_dump(mode="json")

    return _cached({"r": "centre", "id": centre_id}, produce)


def create_centre(db: Session, data: CentreCreate) -> CentreDetailOut:
    centre = DiagnosticCentre(**data.model_dump())
    db.add(centre)
    _commit_or_conflict(db, "A centre with this name and address exists", "CENTRE_ALREADY_EXISTS")
    return _centre_detail(centre, include_inactive=True)


def update_centre(db: Session, centre_id: int, data: CentreUpdate) -> CentreDetailOut:
    centre = _get_centre(db, centre_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(centre, field, value)
    _commit_or_conflict(db, "A centre with this name and address exists", "CENTRE_ALREADY_EXISTS")
    return _centre_detail(centre, include_inactive=True)


def add_test_to_centre(db: Session, centre_id: int, data: CentreTestCreate) -> CentreTestOut:
    _get_centre(db, centre_id)
    if db.get(DiagnosticTest, data.test_id) is None:
        raise NotFoundError(f"Test {data.test_id} not found", code="TEST_NOT_FOUND")
    offering = CentreTest(centre_id=centre_id, test_id=data.test_id, price=data.price)
    db.add(offering)
    _commit_or_conflict(db, "This centre already offers this test", "TEST_ALREADY_OFFERED")
    db.refresh(offering)
    return CentreTestOut.model_validate(offering)


def update_centre_test(
    db: Session, centre_id: int, test_id: int, data: CentreTestUpdate
) -> CentreTestOut:
    offering = db.scalar(
        select(CentreTest).where(CentreTest.centre_id == centre_id, CentreTest.test_id == test_id)
    )
    if offering is None:
        raise NotFoundError("This centre does not offer this test", code="TEST_NOT_OFFERED")
    # Existing bookings keep their snapshotted amount; only new bookings see a new price.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(offering, field, value)
    db.commit()
    _invalidate()
    return CentreTestOut.model_validate(offering)

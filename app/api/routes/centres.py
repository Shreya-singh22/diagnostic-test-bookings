from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AdminUser, DbSession
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
from app.services import centre_service

router = APIRouter(tags=["centres & tests"])

Pagination = Annotated[PageParams, Depends()]


# ----- Public reads --------------------------------------------------------


@router.get("/centres", response_model=Page[CentreOut])
def list_centres(
    db: DbSession,
    page: Pagination,
    city: Annotated[str | None, Query(max_length=100, description="Exact city, case-insensitive")] = None,
    test_id: Annotated[int | None, Query(gt=0, description="Only centres offering this test")] = None,
) -> dict:
    return centre_service.list_centres(db, page, city, test_id)


@router.get("/centres/{centre_id}", response_model=CentreDetailOut)
def get_centre(centre_id: int, db: DbSession) -> dict:
    return centre_service.get_centre_detail(db, centre_id)


@router.get("/tests", response_model=Page[DiagnosticTestOut])
def list_tests(
    db: DbSession,
    page: Pagination,
    q: Annotated[str | None, Query(max_length=100, description="Search name or code")] = None,
) -> dict:
    return centre_service.list_tests(db, page, q)


# ----- Admin writes --------------------------------------------------------


@router.post("/tests", response_model=DiagnosticTestOut, status_code=status.HTTP_201_CREATED)
def create_test(data: DiagnosticTestCreate, db: DbSession, _: AdminUser) -> DiagnosticTestOut:
    return centre_service.create_test(db, data)


@router.post("/centres", response_model=CentreDetailOut, status_code=status.HTTP_201_CREATED)
def create_centre(data: CentreCreate, db: DbSession, _: AdminUser) -> CentreDetailOut:
    return centre_service.create_centre(db, data)


@router.patch("/centres/{centre_id}", response_model=CentreDetailOut)
def update_centre(
    centre_id: int, data: CentreUpdate, db: DbSession, _: AdminUser
) -> CentreDetailOut:
    return centre_service.update_centre(db, centre_id, data)


@router.post(
    "/centres/{centre_id}/tests",
    response_model=CentreTestOut,
    status_code=status.HTTP_201_CREATED,
)
def add_test_to_centre(
    centre_id: int, data: CentreTestCreate, db: DbSession, _: AdminUser
) -> CentreTestOut:
    return centre_service.add_test_to_centre(db, centre_id, data)


@router.patch("/centres/{centre_id}/tests/{test_id}", response_model=CentreTestOut)
def update_centre_test(
    centre_id: int, test_id: int, data: CentreTestUpdate, db: DbSession, _: AdminUser
) -> CentreTestOut:
    return centre_service.update_centre_test(db, centre_id, test_id, data)

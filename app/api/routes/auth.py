from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession
from app.core.rate_limit import rate_limit
from app.core.security import create_access_token
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

auth_rate_limit = Depends(rate_limit("auth", "auth_rate_limit"))


@router.post(
    "/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[auth_rate_limit],
    responses={409: {"description": "Email already registered"}},
)
def signup(data: SignupRequest, db: DbSession) -> UserOut:
    return auth_service.signup(db, data)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[auth_rate_limit],
    responses={401: {"description": "Invalid credentials"}},
)
def login(data: LoginRequest, db: DbSession) -> TokenResponse:
    user = auth_service.authenticate(db, data)
    token, expires_in = create_access_token(user.id)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user

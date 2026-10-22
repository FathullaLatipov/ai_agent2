"""Auth endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.core.deps import CurrentUser, SessionDep, rate_limit
from app.core.security import create_access_token
from app.models import Organization, OrganizationMember
from app.schemas.auth import (
    LoginRequest,
    MembershipOut,
    MeResponse,
    OrganizationOut,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services.auth import AuthError, authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])

_auth_limit = rate_limit("auth")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register", response_model=TokenResponse, dependencies=[Depends(_auth_limit)])
async def register(body: RegisterRequest, session: SessionDep, request: Request):
    try:
        user, _org = await register_user(
            session,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            organization_name=body.organization_name,
            ip_address=_client_ip(request),
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_auth_limit)])
async def login(body: LoginRequest, session: SessionDep, request: Request):
    try:
        user = await authenticate_user(
            session, email=body.email, password=body.password, ip_address=_client_ip(request)
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, session: SessionDep):
    rows = (
        await session.execute(
            select(OrganizationMember, Organization)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == user.id)
        )
    ).all()
    return MeResponse(
        user=UserOut.model_validate(user),
        memberships=[
            MembershipOut(organization=OrganizationOut.model_validate(org), role=m.role)
            for m, org in rows
        ],
    )

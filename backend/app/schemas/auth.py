"""Auth and account schemas."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import MemberRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class MembershipOut(BaseModel):
    organization: OrganizationOut
    role: MemberRole


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]

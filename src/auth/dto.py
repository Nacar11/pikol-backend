from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Request schemas forbid unknown fields (asima's forbidNonWhitelisted): a
# `role` smuggled into a registration body is rejected, not silently dropped.
Forbid = ConfigDict(extra="forbid")

Password = Field(min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    model_config = Forbid

    email: EmailStr
    password: str = Password
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    mobile: str | None = Field(default=None, max_length=32)


class TokenRequest(BaseModel):
    model_config = Forbid

    token: str = Field(min_length=1, max_length=256)


class EmailRequest(BaseModel):
    model_config = Forbid

    email: EmailStr


class LoginRequest(BaseModel):
    model_config = Forbid

    email: EmailStr
    # No length floor here: a password set under an older policy must still
    # be able to log in.
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    model_config = Forbid

    refresh_token: str = Field(min_length=1, max_length=2048)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # access token lifetime, seconds


class PasswordResetRequest(BaseModel):
    model_config = Forbid

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Password


class AcceptInvitationRequest(BaseModel):
    model_config = Forbid

    token: str = Field(min_length=1, max_length=256)
    password: str = Password

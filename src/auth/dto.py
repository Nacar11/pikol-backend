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

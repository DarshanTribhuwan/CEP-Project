"""Registration and authentication schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

MOBILE_PATTERN = r"^[6-9]\d{9}$"


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mobile: str = Field(
        description="10-digit Indian mobile number",
        examples=["9876543210"],
    )
    language: str = Field(default="mr", description="mr | hi | en")
    pin: str = Field(min_length=4, max_length=8, description="numeric PIN", examples=["1234"])

    @field_validator("mobile")
    @classmethod
    def _mobile_shape(cls, value: str) -> str:
        import re

        cleaned = "".join(ch for ch in value if ch.isdigit() or ch == "+").lstrip("+")
        if cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = cleaned[2:]
        if not re.match(MOBILE_PATTERN, cleaned):
            raise ValueError("mobile must be a 10-digit Indian mobile number")
        return cleaned

    @field_validator("pin")
    @classmethod
    def _pin_numeric(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("pin must contain digits only")
        return value


class LoginRequest(BaseModel):
    mobile: str
    pin: str


class FarmerOut(BaseModel):
    id: int
    name: str
    mobile: str
    language: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    farmer: FarmerOut
    note: str = (
        "Development authentication only: a mobile number plus PIN. "
        "Production use requires OTP delivery or another verified second factor."
    )

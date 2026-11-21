"""Password hashing (argon2id) and JWT access tokens (D-016)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.enums import Classification, Role
from app.core.errors import AuthenticationError

_hasher = PasswordHasher()  # argon2id with library defaults (memory-hard)
_ALGORITHM = "HS256"
_ISSUER = "fraud-ring-investigator"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(
    user_id: uuid.UUID, roles: list[Role], clearance: Classification, secret: str, ttl_minutes: int
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "roles": [r.value for r in roles],
        "clr": clearance.value,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "iss": _ISSUER,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, secret, algorithms=[_ALGORITHM], issuer=_ISSUER, options={"require": ["sub", "exp", "iat", "iss"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token") from exc

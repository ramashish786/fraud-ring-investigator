from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import AuthenticationError
from app.core.settings import Settings
from app.infrastructure.database.engine import Database

from .repository import UserRepository
from .security import create_access_token, hash_password, verify_password

# Verifying against a dummy hash when the user doesn't exist keeps response time similar,
# so login timing doesn't reveal which emails are registered.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


class AuthService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def login(self, email: str, password: str) -> tuple[str, int]:
        async with self.db.unit_of_work() as s:
            user = await UserRepository(s).get_by_email(email)
            ok = verify_password(user.password_hash if user else _DUMMY_HASH, password)
            if not user or not ok or not user.is_active:
                raise AuthenticationError("Invalid credentials")
            user.last_login_at = datetime.now(UTC)
            token = create_access_token(
                user.id,
                [r.role_name for r in user.roles],
                user.clearance,
                self.settings.jwt_secret.get_secret_value(),
                self.settings.jwt_access_ttl_minutes,
            )
        return token, self.settings.jwt_access_ttl_minutes * 60

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Classification, Role

from .models import User, UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.s.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))

    async def get_by_email(self, email: str) -> User | None:
        return await self.s.scalar(
            select(User).where(func.lower(User.email) == email.lower(), User.deleted_at.is_(None))
        )

    async def create(
        self, *, email: str, full_name: str, password_hash: str, roles: list[Role], clearance: Classification
    ) -> User:
        user = User(email=email, full_name=full_name, password_hash=password_hash, clearance=clearance)
        self.s.add(user)
        await self.s.flush()
        for r in roles:
            self.s.add(UserRole(user_id=user.id, role_name=r))
        await self.s.flush()
        await self.s.refresh(user, ["roles"])
        return user

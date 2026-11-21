"""The authenticated caller, as seen by domain services (no FastAPI types)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.enums import ActorType, Classification, Role


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID | None
    roles: frozenset[Role] = field(default_factory=frozenset)
    clearance: Classification = Classification.INTERNAL
    actor_type: ActorType = ActorType.USER
    request_id: str | None = None

    def has_any(self, *roles: Role) -> bool:
        return bool(self.roles & set(roles)) or Role.ADMIN in self.roles

    @classmethod
    def system(cls, clearance: Classification = Classification.RESTRICTED) -> Actor:
        return cls(user_id=None, roles=frozenset(), clearance=clearance, actor_type=ActorType.SYSTEM)

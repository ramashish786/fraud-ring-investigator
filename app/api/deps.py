"""FastAPI dependencies: process resources, authentication and role checks."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.core.container import Container
from app.core.enums import Classification, Role
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.domains.identity.actor import Actor
from app.domains.identity.repository import UserRepository
from app.domains.identity.security import decode_access_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]


async def current_actor(request: Request, c: ContainerDep, token: Annotated[str | None, Depends(oauth2)]) -> Actor:
    if not token:
        raise AuthenticationError("Missing bearer token")
    claims = decode_access_token(token, c.settings.jwt_secret.get_secret_value())
    # Re-check the user on every request: deactivated users lose access immediately, and roles/clearance come
    # from the database, not from a possibly stale token.
    async with c.db.read_session() as s:
        user = await UserRepository(s).get(uuid.UUID(claims["sub"]))
    if not user or not user.is_active:
        raise AuthenticationError("User inactive or not found")
    return Actor(
        user_id=user.id,
        roles=frozenset(Role(r.role_name) for r in user.roles),
        clearance=Classification(user.clearance),
        request_id=getattr(request.state, "request_id", None),
    )


ActorDep = Annotated[Actor, Depends(current_actor)]


def require_roles(*roles: Role) -> Callable[..., Coroutine[Any, Any, Actor]]:
    async def check(actor: ActorDep) -> Actor:
        if not actor.has_any(*roles):
            raise PermissionDeniedError(f"Requires one of: {', '.join(r.value for r in roles)}")
        return actor

    return check


READ_ROLES = (Role.ANALYST, Role.REVIEWER, Role.AUDITOR, Role.ADMIN)
Reader = Annotated[Actor, Depends(require_roles(*READ_ROLES))]
Analyst = Annotated[Actor, Depends(require_roles(Role.ANALYST))]
Reviewer = Annotated[Actor, Depends(require_roles(Role.REVIEWER))]
Auditor = Annotated[Actor, Depends(require_roles(Role.AUDITOR, Role.ADMIN))]
Uploader = Annotated[Actor, Depends(require_roles(Role.ANALYST, Role.ADMIN))]

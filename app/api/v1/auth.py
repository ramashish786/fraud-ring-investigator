from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.api.deps import ActorDep, ContainerDep
from app.core.enums import Classification, Role
from app.domains.identity.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int


class Me(BaseModel):
    user_id: uuid.UUID
    roles: list[Role]
    clearance: Classification


@router.post("/token", response_model=TokenResponse, summary="Exchange email + password for an access token")
async def token(c: ContainerDep, form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> TokenResponse:
    access, ttl = await AuthService(c.db, c.settings).login(form.username, form.password)
    return TokenResponse(access_token=access, expires_in=ttl)


@router.get("/me", response_model=Me)
async def me(actor: ActorDep) -> Me:
    assert actor.user_id is not None
    return Me(user_id=actor.user_id, roles=sorted(actor.roles), clearance=actor.clearance)

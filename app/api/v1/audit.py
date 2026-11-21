from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import Auditor, ContainerDep
from app.domains.audit.service import verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])


class ChainStatus(BaseModel):
    rows_checked: int
    intact: bool
    first_broken_id: int | None


@router.get("/verify", response_model=ChainStatus, summary="Recompute the audit hash chain")
async def verify(c: ContainerDep, _: Auditor) -> ChainStatus:
    async with c.db.read_session() as s:
        n, broken = await verify_chain(s)
    return ChainStatus(rows_checked=n, intact=broken is None, first_broken_id=broken)

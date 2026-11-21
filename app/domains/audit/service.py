"""Hash-chained audit log (D-016).

record(): inside the caller's transaction, take a transaction-scoped advisory lock so
chain appends are serialised, read the last hash, and insert the new row with
hash = sha256(prev_hash || canonical_json(row)). Concurrent writers queue on the lock;
audit volume is low, so serialising is an acceptable trade-off for an unbroken chain.

verify(): recompute the whole chain; returns the first broken row id, if any.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.actor import Actor

from .models import AuditLog

_LOCK_KEY = 0x6175646974  # "audit"


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str | None, row: dict[str, Any]) -> str:
    return hashlib.sha256(((prev_hash or "") + _canonical(row)).encode()).hexdigest()


def _payload(log: AuditLog) -> dict[str, Any]:
    return {
        "occurred_at": log.occurred_at.astimezone(UTC).isoformat(),
        "actor_type": str(log.actor_type),
        "actor_user_id": str(log.actor_user_id) if log.actor_user_id else None,
        "action": log.action,
        "entity_type": log.entity_type,
        "entity_id": log.entity_id,
        "request_id": log.request_id,
        "details": log.details,
    }


async def record(
    session: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = await session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
    log = AuditLog(
        occurred_at=datetime.now(UTC).replace(microsecond=0),
        actor_type=actor.actor_type,
        actor_user_id=actor.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=actor.request_id,
        details=details or {},
        prev_hash=prev,
    )
    log.hash = compute_hash(prev, _payload(log))
    session.add(log)
    await session.flush()
    return log


async def verify_chain(session: AsyncSession) -> tuple[int, int | None]:
    """Returns (rows_checked, first_broken_id or None)."""
    prev: str | None = None
    n = 0
    for log in (await session.scalars(select(AuditLog).order_by(AuditLog.id))).all():
        n += 1
        if log.prev_hash != prev or log.hash != compute_hash(prev, _payload(log)):
            return n, log.id
        prev = log.hash
    return n, None

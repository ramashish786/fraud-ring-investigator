"""Job queue operations (D-007).

claim(): one statement picks the oldest runnable job with FOR UPDATE SKIP LOCKED, so
concurrent workers never claim the same row and never block on each other. A job whose
lease expired (worker crashed or was killed) is runnable again; the workflow resumes it
from its last checkpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobKind, JobStatus

from .models import Job

_CLAIM = text("""
UPDATE jobs SET status = 'running', attempts = attempts + 1, leased_by = :worker,
       leased_until = now() + make_interval(secs => :lease), updated_at = now()
WHERE id = (
    SELECT id FROM jobs
    WHERE attempts < max_attempts AND run_after <= now()
      AND (status IN ('queued', 'failed') OR (status = 'running' AND leased_until < now()))
    ORDER BY run_after, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING id, kind, investigation_id, document_id, payload, attempts, max_attempts
""")

# Expired leases with no attempts left: give up explicitly instead of looping forever.
_EXHAUSTED = text("""
UPDATE jobs SET status = 'dead', last_error = coalesce(last_error, 'lease expired; attempts exhausted'),
       updated_at = now()
WHERE status IN ('running', 'failed') AND attempts >= max_attempts
  AND (status = 'failed' OR leased_until < now())
RETURNING id, kind, investigation_id, document_id
""")


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: JobKind
    investigation_id: uuid.UUID | None
    document_id: uuid.UUID | None
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def backoff_seconds(attempts: int) -> int:
    return int(min(300, 5 * 2 ** max(0, attempts - 1)))  # 5s, 10s, 20s ... capped at 5 min


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def enqueue(
        self,
        kind: JobKind,
        *,
        investigation_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 5,
    ) -> Job:
        job = Job(
            kind=kind,
            investigation_id=investigation_id,
            document_id=document_id,
            payload=payload or {},
            max_attempts=max_attempts,
        )
        self.s.add(job)
        await self.s.flush()
        return job

    async def claim(self, worker_id: str, lease_s: int) -> ClaimedJob | None:
        row = (await self.s.execute(_CLAIM, {"worker": worker_id, "lease": lease_s})).mappings().first()
        if not row:
            return None
        return ClaimedJob(
            id=row["id"],
            kind=JobKind(row["kind"]),
            investigation_id=row["investigation_id"],
            document_id=row["document_id"],
            payload=row["payload"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
        )

    async def extend_lease(self, job_id: int, worker_id: str, lease_s: int) -> bool:
        res = await self.s.execute(
            text(
                "UPDATE jobs SET leased_until = now() + make_interval(secs => :lease) "
                "WHERE id = :id AND leased_by = :worker AND status = 'running'"
            ),
            {"id": job_id, "worker": worker_id, "lease": lease_s},
        )
        return bool(res.rowcount)  # type: ignore[attr-defined]

    async def complete(self, job_id: int) -> None:
        await self.s.execute(
            text(
                "UPDATE jobs SET status = 'succeeded', leased_until = NULL, last_error = NULL, updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )

    async def fail(self, job: ClaimedJob, error: str) -> JobStatus:
        status = JobStatus.DEAD if job.attempts >= job.max_attempts else JobStatus.FAILED
        await self.s.execute(
            text(
                "UPDATE jobs SET status = :status, leased_until = NULL, last_error = :err, updated_at = now(), "
                "run_after = now() + make_interval(secs => :delay) WHERE id = :id"
            ),
            {"id": job.id, "status": status.value, "err": error[:2000], "delay": backoff_seconds(job.attempts)},
        )
        return status

    async def reap_exhausted(self) -> list[dict[str, Any]]:
        return [dict(r) for r in (await self.s.execute(_EXHAUSTED)).mappings().all()]

    async def queue_stats(self) -> dict[str, Any]:
        """Runnable depth per kind, age of the oldest runnable job, and dead jobs (metrics + SLOs)."""
        rows = (
            (
                await self.s.execute(
                    text(
                        "SELECT kind, count(*) AS n, extract(epoch FROM now() - min(run_after)) AS oldest FROM jobs "
                        "WHERE status IN ('queued','failed') AND run_after <= now() GROUP BY kind"
                    )
                )
            )
            .mappings()
            .all()
        )
        dead = await self.s.scalar(text("SELECT count(*) FROM jobs WHERE status = 'dead'"))
        return {
            "depth": {r["kind"]: int(r["n"]) for r in rows},
            "oldest_age_s": max((float(r["oldest"]) for r in rows), default=0.0),
            "dead": int(dead or 0),
        }

    async def has_pending(self, kind: JobKind, investigation_id: uuid.UUID) -> bool:
        n = await self.s.scalar(
            text(
                "SELECT count(*) FROM jobs WHERE kind = :k AND investigation_id = :i "
                "AND status IN ('queued','running','failed')"
            ),
            {"k": kind.value, "i": investigation_id},
        )
        return bool(n)

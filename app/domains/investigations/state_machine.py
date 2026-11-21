"""Investigation lifecycle. Every status change goes through `assert_transition`."""

from __future__ import annotations

from app.core.enums import InvestigationStatus as S
from app.core.errors import ConflictError

ALLOWED: dict[S, frozenset[S]] = {
    S.QUEUED: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.RUNNING: frozenset({S.AWAITING_REVIEW, S.COMPLETED, S.FAILED, S.RUNNING}),  # RUNNING->RUNNING: retry/resume
    S.AWAITING_REVIEW: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.FAILED: frozenset({S.QUEUED}),  # manual retry
    S.COMPLETED: frozenset(),
    S.CANCELLED: frozenset(),
}


def assert_transition(current: S, target: S) -> None:
    if target not in ALLOWED[current]:
        raise ConflictError(
            f"Investigation cannot move from {current.value} to {target.value}",
            details={"current": current.value, "target": target.value},
        )

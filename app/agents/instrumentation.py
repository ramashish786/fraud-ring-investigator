"""Node wrapper: events (timeline + node status), logs with investigation_id, trace spans."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from opentelemetry import trace

from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import NODE_DURATION

from .deps import WorkflowDeps
from .state import InvestigationState

log = get_logger("workflow")
tracer = trace.get_tracer("fri.workflow")
NodeFn = Callable[[InvestigationState, WorkflowDeps], Awaitable[dict[str, Any]]]


def instrumented(
    name: str, fn: NodeFn, deps: WorkflowDeps
) -> Callable[[InvestigationState], Awaitable[dict[str, Any]]]:
    async def run(state: InvestigationState) -> dict[str, Any]:
        inv = uuid.UUID(state["investigation_id"])
        rnd = state.get("round", 0)
        structlog.contextvars.bind_contextvars(investigation_id=str(inv), node=name)
        start = time.perf_counter()
        await deps.event(inv, name, "started", round_=rnd)
        with tracer.start_as_current_span(
            f"node.{name}", attributes={"investigation.id": str(inv), "workflow.round": rnd}
        ):
            try:
                deps.faults.maybe_fail(name, str(inv))
                out = await fn(state, deps)
            except Exception as exc:
                ms = round((time.perf_counter() - start) * 1000)
                await deps.event(
                    inv, name, "failed", round_=rnd, duration_ms=ms, detail={"error_type": type(exc).__name__}
                )
                log.warning("node.failed", error_type=type(exc).__name__, duration_ms=ms)
                NODE_DURATION.labels(name, "failed").observe(ms / 1000)
                raise
        ms = round((time.perf_counter() - start) * 1000)
        await deps.event(
            inv, name, "completed", round_=rnd, duration_ms=ms, detail={"errors": len(out.get("errors", []))}
        )
        log.info("node.completed", duration_ms=ms)
        NODE_DURATION.labels(name, "completed").observe(ms / 1000)
        return out

    run.__name__ = name
    return run

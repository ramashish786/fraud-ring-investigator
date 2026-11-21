"""Dependency readiness checks, run concurrently with a per-check timeout.

Liveness (`/health`) says "the process is up" and never touches dependencies, so an
orchestrator doesn't restart a healthy API just because Neo4j is slow. Readiness
(`/health/ready`) says "I can serve traffic" and checks every dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from .logging import get_logger

log = get_logger("health")


class Pingable(Protocol):
    async def ping(self, timeout_s: float) -> float: ...


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Literal["ok", "error"]
    latency_ms: float | None = None
    error: str | None = None  # exception class name only; never hostnames or credentials


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: list[CheckResult]


async def _check(name: str, dep: Pingable, timeout_s: float) -> CheckResult:
    try:
        async with asyncio.timeout(timeout_s):
            latency = await dep.ping(timeout_s)
        return CheckResult(name, "ok", round(latency, 1))
    except TimeoutError:
        return CheckResult(name, "error", error="Timeout")
    except Exception as exc:
        log.warning("health.check_failed", dependency=name, error_type=type(exc).__name__)
        return CheckResult(name, "error", error=type(exc).__name__)


async def check_readiness(deps: Mapping[str, Pingable], timeout_s: float) -> ReadinessReport:
    results = await asyncio.gather(*(_check(n, d, timeout_s) for n, d in deps.items()))
    return ReadinessReport(ready=all(r.status == "ok" for r in results), checks=list(results))

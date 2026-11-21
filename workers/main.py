"""Worker process:  python -m workers.main

Loop: claim a job (SELECT … FOR UPDATE SKIP LOCKED, D-007) → run it under a renewable
lease → mark succeeded, or failed with backoff, or dead after max attempts.

Crash safety: if this process dies mid-job, the lease expires and another worker claims
the job; the workflow continues from its last LangGraph checkpoint, and evidence writes
are idempotent, so completed work is neither lost nor duplicated.

Graceful shutdown (SIGTERM): stop claiming, give running jobs `shutdown_grace_s` to finish,
then cancel them. Their leases expire and they are retried from the checkpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from prometheus_client import start_http_server

from app.agents.checkpoint import open_checkpointer
from app.agents.deps import FaultInjector, WorkflowDeps
from app.agents.runner import InvestigationRunner
from app.agents.workflow import build_workflow
from app.core.container import Container
from app.core.enums import ActorType, InvestigationStatus, JobKind, JobStatus
from app.core.enums import JobKind as _JobKind
from app.core.settings import Settings, get_settings
from app.domains.audit.service import record
from app.domains.documents.service import IngestionService
from app.domains.identity.actor import Actor
from app.domains.investigations.repository import InvestigationRepository
from app.domains.risk.engine import RiskEngine
from app.infrastructure.jobs.repository import ClaimedJob, JobRepository
from app.infrastructure.observability.logging import configure_logging, get_logger
from app.infrastructure.observability.metrics import (
    DEAD_JOBS,
    JOB_DURATION,
    JOBS_PROCESSED,
    QUEUE_DEPTH,
    QUEUE_OLDEST_AGE,
    WORKER_RUNNING,
)
from app.infrastructure.observability.tracing import configure_tracing

log = get_logger("worker")


class Worker:
    def __init__(
        self,
        c: Container,
        runner: InvestigationRunner,
        ingestion: IngestionService,
        *,
        concurrency: int = 2,
        lease_s: int = 120,
        poll_interval_s: float = 2.0,
        shutdown_grace_s: float = 20.0,
    ) -> None:
        self.c, self.runner, self.ingestion = c, runner, ingestion
        self.concurrency, self.lease_s, self.poll_interval_s = concurrency, lease_s, poll_interval_s
        self.shutdown_grace_s = shutdown_grace_s
        self.worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    def stop(self) -> None:
        log.info("worker.stop_requested", worker_id=self.worker_id)
        self._stop.set()

    async def _claim(self) -> ClaimedJob | None:
        async with self.c.db.unit_of_work() as s:
            return await JobRepository(s).claim(self.worker_id, self.lease_s)

    async def _keep_lease(self, job: ClaimedJob) -> None:
        while True:
            await asyncio.sleep(self.lease_s / 3)
            async with self.c.db.unit_of_work() as s:
                if not await JobRepository(s).extend_lease(job.id, self.worker_id, self.lease_s):
                    log.warning("worker.lease_lost", job_id=job.id)
                    return

    async def _handle(self, job: ClaimedJob) -> None:
        if job.kind == JobKind.START_INVESTIGATION:
            assert job.investigation_id
            await self.runner.start(job.investigation_id)
        elif job.kind == JobKind.RESUME_INVESTIGATION:
            assert job.investigation_id
            await self.runner.resume(job.investigation_id, job.payload)
        elif job.kind == JobKind.INGEST_DOCUMENT:
            assert job.document_id
            await self.ingestion.process(job.document_id)

    async def _on_dead(self, kind: JobKind, investigation_id: uuid.UUID | None, error: str) -> None:
        if investigation_id is None or kind == JobKind.INGEST_DOCUMENT:
            return
        async with self.c.db.unit_of_work() as s:
            repo = InvestigationRepository(s)
            inv = await repo.get(investigation_id, for_update=True)
            if inv and inv.status not in InvestigationStatus.terminal():
                await repo.transition(
                    inv,
                    InvestigationStatus.FAILED,
                    actor_type=ActorType.SYSTEM,
                    reason=f"job exhausted retries: {error[:200]}",
                )
                await record(
                    s,
                    Actor.system(),
                    "investigation.failed",
                    "investigation",
                    str(investigation_id),
                    {"error": error[:200]},
                )

    async def execute(self, job: ClaimedJob) -> None:
        structlog.contextvars.bind_contextvars(
            job_id=job.id, job_kind=job.kind.value, investigation_id=str(job.investigation_id or "")
        )
        lease = asyncio.create_task(self._keep_lease(job))
        started = time.perf_counter()
        WORKER_RUNNING.inc()
        outcome = "succeeded"
        try:
            await self._handle(job)
            async with self.c.db.unit_of_work() as s:
                await JobRepository(s).complete(job.id)
            log.info("job.succeeded", attempts=job.attempts)
        except asyncio.CancelledError:
            outcome = "cancelled"
            log.warning("job.cancelled_on_shutdown")  # lease expires -> retried from checkpoint
            raise
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            async with self.c.db.unit_of_work() as s:
                status = await JobRepository(s).fail(job, err)
            outcome = status.value
            log.warning("job.failed", status=status.value, attempts=job.attempts, error_type=type(exc).__name__)
            if status == JobStatus.DEAD:
                await self._on_dead(job.kind, job.investigation_id, err)
        finally:
            lease.cancel()
            WORKER_RUNNING.dec()
            JOBS_PROCESSED.labels(job.kind.value, outcome).inc()
            JOB_DURATION.labels(job.kind.value).observe(time.perf_counter() - started)
            structlog.contextvars.unbind_contextvars("job_id", "job_kind", "investigation_id")

    async def run_once(self) -> bool:
        """Claim and fully execute one job (used by tests and the evaluation harness)."""
        job = await self._claim()
        if job is None:
            return False
        await self.execute(job)
        return True

    async def refresh_queue_metrics(self) -> None:
        async with self.c.db.read_session() as s:
            stats = await JobRepository(s).queue_stats()
        for kind in _JobKind:
            QUEUE_DEPTH.labels(kind.value).set(stats["depth"].get(kind.value, 0))
        QUEUE_OLDEST_AGE.set(stats["oldest_age_s"])
        DEAD_JOBS.set(stats["dead"])

    async def _reap(self) -> None:
        async with self.c.db.unit_of_work() as s:
            dead = await JobRepository(s).reap_exhausted()
        for d in dead:
            await self._on_dead(JobKind(d["kind"]), d["investigation_id"], "lease expired; attempts exhausted")

    async def run(self) -> None:
        log.info("worker.started", worker_id=self.worker_id, concurrency=self.concurrency)
        ticks = 0
        while not self._stop.is_set():
            try:
                if len(self._tasks) < self.concurrency and (job := await self._claim()):
                    task = asyncio.create_task(self.execute(job))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                    continue
                ticks += 1
                if ticks % 5 == 0:
                    await self.refresh_queue_metrics()
                if ticks % 15 == 0:
                    await self._reap()
            except Exception as exc:
                log.warning("worker.loop_error", error_type=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
        if self._tasks:
            log.info("worker.draining", running=len(self._tasks))
            _, pending = await asyncio.wait(self._tasks, timeout=self.shutdown_grace_s)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        log.info("worker.stopped", worker_id=self.worker_id)


async def _warm_memory_store(settings: Settings, c: Container) -> None:
    """The in-memory chunk store (tests/offline only, D-026) doesn't persist across restarts: reload the
    generated corpus so retrieval works after a worker restart. Neo4j deployments never take this path."""
    from pathlib import Path

    from app.evaluation.retrieval import ingest_corpus
    from app.rag.store import InMemoryChunkStore

    manifest = Path(settings.memory_dataset_dir) / "documents/manifest.json"
    if isinstance(c.chunks, InMemoryChunkStore) and not c.chunks.chunks and manifest.exists():
        assert c.embedder is not None
        await ingest_corpus(Path(settings.memory_dataset_dir), c.chunks, c.embedder)
        log.info("worker.memory_corpus_loaded", chunks=len(c.chunks.chunks))


@asynccontextmanager
async def build_worker(
    settings: Settings, container: Container | None = None, faults: FaultInjector | None = None
) -> AsyncIterator[Worker]:
    c = container or Container.build(settings)
    assert c.graph and c.llm and c.chunks and c.embedder and c.storage
    await _warm_memory_store(settings, c)
    async with open_checkpointer(settings.database_url.get_secret_value()) as saver:
        deps = WorkflowDeps(
            settings=settings,
            db=c.db,
            graph=c.graph,
            search=c.search,
            llm=c.llm,
            risk=RiskEngine.from_yaml(),
            faults=faults or FaultInjector(),
        )
        graph = build_workflow(deps, saver)
        worker = Worker(
            c,
            InvestigationRunner(c.db, graph),
            IngestionService(c.db, c.storage, c.chunks, c.embedder),
            concurrency=settings.worker_concurrency,
            lease_s=settings.job_lease_s,
            poll_interval_s=settings.worker_poll_interval_s,
        )
        try:
            yield worker
        finally:
            if container is None:
                await c.aclose()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_tracing(settings, service_name="fri-worker")
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)  # Prometheus scrapes worker:9101/metrics
    async with build_worker(settings) as worker:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

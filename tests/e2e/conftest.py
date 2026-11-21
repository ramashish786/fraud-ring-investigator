from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from app.agents.checkpoint import setup_checkpointer
from app.agents.deps import FaultInjector
from app.core.container import Container
from app.core.settings import Settings
from app.evaluation.retrieval import ingest_corpus
from simulation.config import SimConfig
from simulation.documents import generate
from simulation.simulate import run, write
from tests.integration.conftest import db, make_actor  # noqa: F401
from workers.main import Worker, build_worker


@pytest.fixture(scope="session")
def bank_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("e2e-bank")
    write(run(SimConfig(seed=21, n_customers=600, days=40)), out)
    generate(out)
    return out


@pytest.fixture
def e2e_settings(make_settings: Callable[..., Settings], migrated_db: str, bank_dir: Path, tmp_path: Path) -> Settings:
    return make_settings(
        database_url=migrated_db,
        graph_backend="memory",
        memory_dataset_dir=str(bank_dir),
        document_storage_dir=str(tmp_path / "docs"),
        embedding_dimensions=256,
        workflow_max_rounds=2,
    )


@pytest.fixture
async def worker_factory(e2e_settings: Settings, db: Any, bank_dir: Path) -> AsyncIterator[Any]:  # noqa: F811
    await setup_checkpointer(e2e_settings.database_url.get_secret_value())
    container = Container.build(e2e_settings)
    assert container.chunks and container.embedder
    await ingest_corpus(bank_dir, container.chunks, container.embedder)
    stack: list[Any] = []

    async def make(faults: FaultInjector | None = None) -> Worker:
        cm = build_worker(e2e_settings, container, faults)
        stack.append(cm)
        return await cm.__aenter__()

    yield make
    for cm in stack:
        await cm.__aexit__(None, None, None)
    await container.aclose()


@pytest.fixture
def alerts(bank_dir: Path) -> list[dict[str, Any]]:
    a = json.loads((bank_dir / "alerts/alerts.json").read_text())
    t = {x["external_ref"]: x for x in json.loads((bank_dir / "labels/alert_truth.json").read_text())}
    return [{**x, "truth": t[x["external_ref"]]} for x in a]

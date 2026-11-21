"""Contract-test fixtures: one small deterministic dataset, loaded into every backend.

* memory: always.
* neo4j:  only when TEST_NEO4J_URI is set (e.g. `docker compose --profile test run --rm tests`).
          The test database is WIPED and reloaded, so never point this at real data.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.graph.dataset import BankDataset
from app.graph.memory_repository import InMemoryGraphRepository
from simulation.config import SimConfig
from simulation.simulate import run, write

NEO4J_URI = os.environ.get("TEST_NEO4J_URI")


@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("bank")
    write(run(SimConfig(seed=11, n_customers=500, days=40)), out)
    return out


@pytest.fixture(scope="session")
def dataset(dataset_dir: Path) -> BankDataset:
    return BankDataset.load(dataset_dir)


@pytest.fixture(scope="session")
def spec(dataset_dir: Path) -> dict[str, Any]:
    return json.loads((dataset_dir / "labels/spec_example.json").read_text())


def _neo4j_repo(dataset: BankDataset) -> Any:
    from app.core.settings import Settings
    from app.graph.loader import WIPE, load
    from app.graph.neo4j_repository import Neo4jGraphRepository
    from app.graph.schema.bootstrap import apply_schema
    from app.infrastructure.neo4j.client import Neo4jClient

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused/unused",
        jwt_secret="x" * 40,  # type: ignore[call-arg]
        neo4j_uri=NEO4J_URI,
        neo4j_password=os.environ.get("TEST_NEO4J_PASSWORD", "neo4j_dev_pw"),
        embedding_dimensions=64,
    )

    async def prepare() -> None:
        client = Neo4jClient.from_settings(settings)
        try:
            await apply_schema(client, settings.embedding_dimensions)
            await client.run_schema(WIPE)
            await load(client, dataset)
        finally:
            await client.close()

    asyncio.run(prepare())
    return Neo4jGraphRepository(Neo4jClient.from_settings(settings))


@pytest.fixture(scope="session", params=["memory", "neo4j"])
def graph_repo(request: pytest.FixtureRequest, dataset: BankDataset) -> Any:
    if request.param == "memory":
        return InMemoryGraphRepository(dataset)
    if not NEO4J_URI:
        if os.environ.get("REQUIRE_NEO4J") == "1":
            # CI sets this so the Neo4j half of the contract can never be skipped silently (D-054).
            pytest.fail("REQUIRE_NEO4J=1 but TEST_NEO4J_URI is not set")
        pytest.skip("neo4j backend: set TEST_NEO4J_URI to run the contract tests against Neo4j")
    return _neo4j_repo(dataset)

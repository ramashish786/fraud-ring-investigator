import pytest

from app.graph.memory_repository import InMemoryGraphRepository
from tests.contract.conftest import dataset, dataset_dir, spec  # noqa: F401  (reuse the session dataset)


@pytest.fixture(scope="session")
def repo(dataset):  # type: ignore[no-untyped-def]  # noqa: F811
    return InMemoryGraphRepository(dataset)

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def test_secrets_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATABASE_URL", "NEO4J_PASSWORD", "JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None)  # type: ignore[call-arg]
    missing = {err["loc"][0] for err in e.value.errors()}
    assert {"database_url", "neo4j_password", "jwt_secret"} <= missing


def test_openai_provider_requires_key_and_models(make_settings) -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        make_settings(llm_provider="openai")
    s = make_settings(
        llm_provider="openai", openai_api_key="sk-test", openai_chat_model="m", openai_embedding_model="e"
    )
    assert s.openai_chat_model == "m"


def test_prod_rejects_weak_jwt_secret(make_settings) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make_settings(env="prod", jwt_secret="short")


def test_secrets_do_not_leak_in_repr(make_settings) -> None:
    s = make_settings()
    assert "test-neo4j-pw" not in repr(s)
    assert "fri:fri" not in repr(s)


def test_empty_openai_key_counts_as_missing(make_settings) -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        make_settings(llm_provider="openai", openai_api_key="", openai_chat_model="m", openai_embedding_model="e")

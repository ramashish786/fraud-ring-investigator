"""Centralised LLM service (spec §8, D-010, D-027).

An `LLMTask` bundles everything about one kind of call: the output schema (structured
output), how to render the input into messages, a validator (e.g. "every cited evidence
ref must exist"), and a deterministic fake used by tests and offline runs. Nodes call
`llm.run(task, data)` and never see provider SDK types.

Reliability: per-call timeout, provider retries for 429/5xx (SDK max_retries), one
corrective re-ask when the output fails validation, a concurrency semaphore, and a row
in `llm_calls` for every call (task, model, status, latency, tokens; never prompt text).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from opentelemetry import trace
from pydantic import BaseModel

from app.core.settings import Settings
from app.infrastructure.database.engine import Database
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import LLM_CALLS, LLM_LATENCY, LLM_TOKENS, LLM_VALIDATION_PROBLEMS

from .models import LLMCall

log = get_logger("llm")
tracer = trace.get_tracer("fri.llm")
InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)


@dataclass(frozen=True)
class LLMTask(Generic[InT, OutT]):
    name: str
    output_schema: type[OutT]
    system: str
    render: Callable[[InT], str]
    fake: Callable[[InT], OutT]
    validate: Callable[[InT, OutT], list[str]] | None = None


@dataclass
class LLMResult(Generic[OutT]):
    output: OutT
    problems: list[str] = field(default_factory=list)  # validation problems left after the corrective retry
    model: str = ""
    attempts: int = 1


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMService(Protocol):
    provider: str
    model: str

    async def run(
        self, task: LLMTask[InT, OutT], data: InT, *, investigation_id: uuid.UUID | None = None
    ) -> LLMResult[OutT]: ...


class StructuredOutputError(Exception):
    pass


class _BaseLLMService:
    provider = "base"
    model = "none"

    def __init__(self, db: Database | None, max_concurrency: int, timeout_s: float) -> None:
        self._db = db
        self._sem = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_s

    async def _call(self, task: LLMTask[InT, OutT], data: InT, messages: list[tuple[str, str]]) -> tuple[OutT, Usage]:
        raise NotImplementedError

    async def _record(
        self,
        investigation_id: uuid.UUID | None,
        task: str,
        status: str,
        latency_ms: int,
        usage: Usage,
        error_type: str | None = None,
    ) -> None:
        LLM_CALLS.labels(task, status).inc()
        LLM_LATENCY.labels(task).observe(latency_ms / 1000)
        if usage.prompt_tokens:
            LLM_TOKENS.labels(task, "prompt").inc(usage.prompt_tokens)
        if usage.completion_tokens:
            LLM_TOKENS.labels(task, "completion").inc(usage.completion_tokens)
        if self._db is None:
            return
        try:
            async with self._db.unit_of_work() as s:
                s.add(
                    LLMCall(
                        investigation_id=investigation_id,
                        task=task,
                        provider=self.provider,
                        model=self.model,
                        status=status,
                        latency_ms=latency_ms,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens,
                        error_type=error_type,
                    )
                )
        except Exception as exc:
            log.warning("llm.record_failed", error_type=type(exc).__name__)

    async def run(
        self, task: LLMTask[InT, OutT], data: InT, *, investigation_id: uuid.UUID | None = None
    ) -> LLMResult[OutT]:
        messages = [("system", task.system), ("user", task.render(data))]
        problems: list[str] = []
        for attempt in (1, 2):
            start = time.perf_counter()
            usage = Usage()
            with tracer.start_as_current_span(
                f"llm.{task.name}",
                attributes={"llm.provider": self.provider, "llm.model": self.model, "llm.attempt": attempt},
            ) as span:
                try:
                    async with self._sem, asyncio.timeout(self._timeout):
                        out, usage = await self._call(task, data, messages)
                except TimeoutError:
                    await self._record(investigation_id, task.name, "timeout", _ms(start), usage, "TimeoutError")
                    raise
                except StructuredOutputError as exc:
                    await self._record(
                        investigation_id, task.name, "invalid_output", _ms(start), usage, type(exc).__name__
                    )
                    if attempt == 2:
                        raise
                    messages.append(
                        (
                            "user",
                            f"Your previous answer was not valid JSON for the schema: {exc}. "
                            "Answer again, following the schema exactly.",
                        )
                    )
                    continue
                except Exception as exc:
                    await self._record(investigation_id, task.name, "error", _ms(start), usage, type(exc).__name__)
                    raise
                problems = task.validate(data, out) if task.validate else []
                span.set_attribute("llm.total_tokens", usage.total_tokens or 0)
                span.set_attribute("llm.problems", len(problems))
            latency = _ms(start)
            await self._record(investigation_id, task.name, "ok" if not problems else "invalid_output", latency, usage)
            log.info(
                "llm.call",
                task=task.name,
                model=self.model,
                attempt=attempt,
                latency_ms=latency,
                total_tokens=usage.total_tokens,
                problems=len(problems),
            )
            if not problems or attempt == 2:
                if problems:
                    LLM_VALIDATION_PROBLEMS.labels(task.name).inc(len(problems))
                return LLMResult(output=out, problems=problems, model=self.model, attempts=attempt)
            messages.append(("assistant", out.model_dump_json()))
            messages.append(
                (
                    "user",
                    "Fix these problems and answer again. Only cite evidence refs that appear in "
                    "the input; drop claims you cannot support.\n- " + "\n- ".join(problems),
                )
            )
        raise AssertionError("unreachable")


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


class FakeLLMService(_BaseLLMService):
    """Deterministic provider: each task's `fake` builds a schema-valid answer from the input."""

    provider = "fake"
    model = "fake-deterministic"

    async def _call(self, task: LLMTask[InT, OutT], data: InT, messages: list[tuple[str, str]]) -> tuple[OutT, Usage]:
        await asyncio.sleep(0)
        return task.fake(data), Usage(0, 0, 0)


class OpenAILLMService(_BaseLLMService):
    provider = "openai"

    def __init__(self, settings: Settings, db: Database | None) -> None:
        super().__init__(db, settings.llm_max_concurrency, settings.llm_timeout_s)
        from langchain_openai import ChatOpenAI

        assert settings.openai_api_key and settings.openai_chat_model
        self.model = settings.openai_chat_model
        self._chat = ChatOpenAI(
            model=self.model,
            api_key=settings.openai_api_key,
            temperature=0,
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )

    async def _call(self, task: LLMTask[InT, OutT], data: InT, messages: list[tuple[str, str]]) -> tuple[OutT, Usage]:
        runnable = self._chat.with_structured_output(task.output_schema, method="json_schema", include_raw=True)
        res: dict[str, Any] = await runnable.ainvoke(messages)  # type: ignore[assignment,unused-ignore]
        meta = getattr(res.get("raw"), "usage_metadata", None) or {}
        usage = Usage(meta.get("input_tokens"), meta.get("output_tokens"), meta.get("total_tokens"))
        if res.get("parsing_error") or res.get("parsed") is None:
            raise StructuredOutputError(str(res.get("parsing_error") or "no parsed output"))
        parsed = res["parsed"]
        out = parsed if isinstance(parsed, task.output_schema) else task.output_schema.model_validate(parsed)
        return out, usage


def build_llm(settings: Settings, db: Database | None) -> LLMService:
    if settings.llm_provider == "openai":
        return OpenAILLMService(settings, db)
    return FakeLLMService(db, settings.llm_max_concurrency, settings.llm_timeout_s)


def as_json(obj: Any) -> str:
    return json.dumps(obj, indent=1, default=str, ensure_ascii=False)

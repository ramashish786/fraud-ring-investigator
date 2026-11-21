import asyncio

from app.infrastructure.observability.health import check_readiness


class Ok:
    async def ping(self, timeout_s: float) -> float:
        return 1.23


class Boom:
    async def ping(self, timeout_s: float) -> float:
        raise ConnectionRefusedError("db at 10.0.0.5:5432 secret")


class Slow:
    async def ping(self, timeout_s: float) -> float:
        await asyncio.sleep(10)
        return 0.0


async def test_all_ok() -> None:
    r = await check_readiness({"a": Ok(), "b": Ok()}, timeout_s=1)
    assert r.ready and [c.status for c in r.checks] == ["ok", "ok"]


async def test_failure_is_reported_without_internal_details() -> None:
    r = await check_readiness({"a": Ok(), "b": Boom()}, timeout_s=1)
    assert not r.ready
    b = r.checks[1]
    assert b.status == "error" and b.error == "ConnectionRefusedError"


async def test_checks_run_concurrently_and_time_out() -> None:
    loop = asyncio.get_running_loop()
    start = loop.time()
    r = await check_readiness({"s1": Slow(), "s2": Slow()}, timeout_s=0.2)
    assert loop.time() - start < 1.0  # concurrent, not 2 x timeout
    assert [c.error for c in r.checks] == ["Timeout", "Timeout"]

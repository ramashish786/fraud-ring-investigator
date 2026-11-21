"""Behavioural contract for every GraphRepository implementation (D-026)."""

from __future__ import annotations

from typing import Any

import pytest

from app.graph.models import MAX_LIMIT


async def test_account_summaries(graph_repo: Any, spec: dict[str, Any]) -> None:
    rows = await graph_repo.account_summaries([spec["account_a"], "A_DOES_NOT_EXIST"])
    assert [r.account_id for r in rows] == [spec["account_a"]]
    assert rows[0].customer_id == spec["customer_a"]
    assert rows[0].address_key


async def test_transactions_both_directions_oldest_first_and_limited(graph_repo: Any, spec: dict[str, Any]) -> None:
    txns = await graph_repo.transactions(spec["account_a"], limit=500)
    assert txns == sorted(txns, key=lambda t: t.ts)
    ins = [t for t in txns if t.dst_account == spec["account_a"]]
    outs = [t for t in txns if t.src_account == spec["account_a"] and t.dst_account in spec["beneficiaries"]]
    assert len(ins) >= 5 and len(outs) == 3
    assert len(await graph_repo.transactions(spec["account_a"], limit=2)) == 2
    assert await graph_repo.transactions("A_DOES_NOT_EXIST") == []


async def test_money_trail_out_reaches_the_three_beneficiaries(graph_repo: Any, spec: dict[str, Any]) -> None:
    edges = await graph_repo.money_trail(spec["account_a"], direction="out", depth=1, min_amount=10_000)
    assert {e.dst for e in edges if e.hop == 1} >= set(spec["beneficiaries"])
    assert all(e.src == spec["account_a"] for e in edges)


async def test_money_trail_in_and_depth_clamp(graph_repo: Any, spec: dict[str, Any]) -> None:
    edges = await graph_repo.money_trail(spec["beneficiaries"][0], direction="in", depth=2)
    assert any(e.src == spec["account_a"] and e.hop == 1 for e in edges)
    assert any(e.hop == 2 for e in edges)  # the senders into A
    deep = await graph_repo.money_trail(spec["account_a"], direction="out", depth=99)
    assert all(1 <= e.hop <= 4 for e in deep)


async def test_shared_device_links_the_beneficiaries(graph_repo: Any, spec: dict[str, Any]) -> None:
    devices = await graph_repo.shared_devices([spec["beneficiaries"][0]])
    shared = next(d for d in devices if d.device_id == spec["shared_device"])
    owners = {a for u in shared.users for a in u.account_ids}
    assert set(spec["beneficiaries"]) <= owners
    assert shared.user_count >= 3
    assert all(u.first_seen and u.observations > 0 for u in shared.users)  # provenance present


async def test_shared_ip_reports_kind(graph_repo: Any, spec: dict[str, Any]) -> None:
    ips = await graph_repo.shared_ips(spec["beneficiaries"])
    vpn = next(i for i in ips if i.ip == spec["shared_ip"])
    assert vpn.kind == "vpn" and not vpn.is_shared_infrastructure


async def test_beneficiaries_and_fan_in(graph_repo: Any, spec: dict[str, Any]) -> None:
    bens = await graph_repo.beneficiaries([spec["account_a"]])
    added = {b.target_account for b in bens if b.source == "beneficiary_added"}
    assert added == set(spec["beneficiaries"])
    assert bens == sorted(bens, key=lambda b: b.added_at, reverse=True)
    fan_in = await graph_repo.beneficiary_fan_in(spec["beneficiaries"])
    assert all(fan_in[b] >= 1 for b in spec["beneficiaries"])


async def test_prior_cases_distinguish_confirmed_from_inconclusive(graph_repo: Any, spec: dict[str, Any]) -> None:
    cases = await graph_repo.prior_cases(spec["beneficiaries"])
    by_acct = {c.account_id: c.status for c in cases}
    b1, b2, b3 = spec["beneficiaries"]
    assert by_acct.get(b2) == "confirmed_fraud"
    assert by_acct.get(b3) == "inconclusive"
    assert b1 not in by_acct


@pytest.mark.parametrize("limit", [1, 10_000])
async def test_limits_are_clamped(graph_repo: Any, spec: dict[str, Any], limit: int) -> None:
    rows = await graph_repo.transactions(spec["account_a"], limit=limit)
    assert 1 <= len(rows) <= min(max(limit, 1), MAX_LIMIT)


async def test_upsert_investigation_is_idempotent(graph_repo: Any, spec: dict[str, Any]) -> None:
    for _ in range(2):
        await graph_repo.upsert_investigation("inv-contract-1", "running", [spec["account_a"]])

"""Deterministic analysis on the spec example and on benign look-alikes."""

from __future__ import annotations

import json
from typing import Any

from app.core.enums import AssertionLevel
from app.domains.analysis.network import analyze_network
from app.domains.analysis.transactions import analyze_transactions


async def test_spec_example_transactions(repo: Any, spec: dict[str, Any]) -> None:
    res = await analyze_transactions(repo, spec["account_a"])
    assert res.signals["pass_through_events"].value >= 1
    assert res.signals["structured_deposits"].value == 2
    assert all(
        e.assertion_level in (AssertionLevel.VERIFIED_FACT, AssertionLevel.INFERRED_PATTERN) for e in res.evidence
    )
    refs = {e.ref for e in res.evidence}
    assert all(set(s.evidence_refs) <= refs for s in res.signals.values())  # every signal cites real evidence


async def test_spec_example_network(repo: Any, spec: dict[str, Any]) -> None:
    res = await analyze_network(repo, spec["account_a"])
    s = res.signals
    assert s["new_beneficiary_transfers"].value == 3
    assert s["beneficiaries_sharing_identifiers"].value == 3
    assert s["linked_confirmed_fraud_cases"].value == 1
    assert s["linked_inconclusive_cases"].value == 1
    assert set(spec["beneficiaries"]) <= set(res.network_accounts)
    kinds = {e.kind for e in res.evidence}
    assert {"beneficiary", "shared_device", "shared_ip", "prior_case", "money_flow", "beneficiary_cluster"} <= kinds
    confirmed = [e for e in res.evidence if e.kind == "prior_case" and e.snapshot["status"] == "confirmed_fraud"]
    assert confirmed[0].assertion_level == AssertionLevel.CONFIRMED_OUTCOME


async def test_household_device_is_context_not_signal(repo: Any, dataset_dir: Any) -> None:
    traps = json.loads((dataset_dir / "labels/traps.json").read_text())
    accounts = {r["customer_id"]: r["account_id"] for r in repo.accounts.values()}
    household_customer = sorted(traps["household_shared_device"])[0]
    res = await analyze_network(repo, accounts[household_customer], ops=["shared_devices"])
    assert res.signals["household_shared_devices"].value >= 1
    assert res.signals["shared_device_links"].value == 0
    dev = next(e for e in res.evidence if e.kind == "shared_device")
    assert dev.snapshot["benign_context"] == "same_household"


async def test_carrier_nat_is_not_a_risky_ip(repo: Any, dataset_dir: Any) -> None:
    traps = json.loads((dataset_dir / "labels/traps.json").read_text())
    nat_ip = sorted(traps["shared_carrier_or_wifi_ip"])[0]
    user = next(iter(repo.ip_users[nat_ip]))
    res = await analyze_network(repo, repo.cust_accounts[user][0], ops=["shared_ips"])
    assert res.signals["risky_shared_ips"].value == 0
    assert res.signals["shared_infrastructure_ips"].value >= 1


async def test_unknown_account_reports_missing_information(repo: Any) -> None:
    res = await analyze_network(repo, "A_UNKNOWN")
    assert res.evidence == [] and any("not found" in n for n in res.notes)

"""Graph investigator logic: fraud-network discovery around the alert subject (deterministic).

Key rule (spec §2): a shared device or IP is an OBSERVED_RELATIONSHIP, never proof.
Each one is recorded with its benign context (same household, carrier NAT, public
wifi, corporate egress), and only links *without* a benign explanation become signals.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Literal

from app.core.enums import AssertionLevel, EvidenceSource
from app.domains.evidence.schemas import EvidenceItem
from app.graph.models import RISKY_IP_KINDS, BeneficiaryLink, CaseLink, SharedDevice, SharedIP
from app.graph.repository import GraphRepository

from .signals import AnalysisResult

NetworkOp = Literal["money_trail", "shared_devices", "shared_ips", "beneficiary_network", "prior_cases"]
ALL_NETWORK_OPS: tuple[NetworkOp, ...] = (
    "money_trail",
    "shared_devices",
    "shared_ips",
    "beneficiary_network",
    "prior_cases",
)
NEW_BENEFICIARY_WINDOW = timedelta(hours=24)
NEW_DEVICE_WINDOW = timedelta(hours=48)


def _household(users_address_keys: list[str | None]) -> bool:
    keys = {k for k in users_address_keys if k}
    return len(keys) == 1 and None not in users_address_keys


def _device_evidence(d: SharedDevice) -> tuple[EvidenceItem, bool]:
    # a household is small (<= 4 people) and shares one registered address
    household = d.user_count <= 4 and d.user_count == len(d.users) and _household([u.address_key for u in d.users])
    context = "same_household" if household else None
    users = [
        {
            "customer_id": u.customer_id,
            "account_ids": u.account_ids,
            "first_seen": u.first_seen,
            "last_seen": u.last_seen,
            "observations": u.observations,
        }
        for u in d.users
    ]
    note = " (all users share one registered address: likely a household device)" if household else ""
    return EvidenceItem(
        kind="shared_device",
        assertion_level=AssertionLevel.OBSERVED_RELATIONSHIP,
        source=EvidenceSource.NEO4J,
        source_ref={"device_id": d.device_id, "relationship": "USES", "source_ref": "logins,transactions"},
        snapshot={
            "device_id": d.device_id,
            "emulator": d.emulator,
            "os": d.os,
            "user_count": d.user_count,
            "users": users,
            "ip_kinds": d.ip_kinds,
            "benign_context": context,
        },
        summary=f"Device {d.device_id}{' (emulator)' if d.emulator else ''} used by {d.user_count} customers{note}",
    ), household


def _ip_evidence(ip: SharedIP) -> EvidenceItem:
    context = "shared_infrastructure" if ip.is_shared_infrastructure else None
    note = f" ({ip.kind}: shared network, not an identity link)" if context else f" ({ip.kind})"
    return EvidenceItem(
        kind="shared_ip",
        assertion_level=AssertionLevel.OBSERVED_RELATIONSHIP,
        source=EvidenceSource.NEO4J,
        source_ref={"ip": ip.ip, "relationship": "LOGGED_IN_FROM", "source_ref": "logins"},
        snapshot={
            "ip": ip.ip,
            "kind": ip.kind,
            "country": ip.country,
            "user_count": ip.user_count,
            "users": [{"customer_id": u.customer_id, "account_ids": u.account_ids} for u in ip.users],
            "benign_context": context,
        },
        summary=f"IP {ip.ip} used by {ip.user_count} customers{note}",
    )


def _case_evidence(c: CaseLink) -> EvidenceItem:
    level = (
        AssertionLevel.CONFIRMED_OUTCOME if c.status in ("confirmed_fraud", "cleared") else AssertionLevel.VERIFIED_FACT
    )
    return EvidenceItem(
        kind="prior_case",
        assertion_level=level,
        source=EvidenceSource.NEO4J,
        source_ref={"case_id": c.case_id, "account_id": c.account_id, "relationship": "INVOLVED_IN"},
        snapshot={
            "case_id": c.case_id,
            "typology": c.typology,
            "status": c.status,
            "role": c.role,
            "closed_at": c.closed_at,
            "summary": c.summary,
        },
        summary=f"{c.account_id} was {c.role or 'involved'} in {c.case_id} ({c.typology}), outcome: "
        f"{c.status.replace('_', ' ')}",
    )


async def analyze_network(
    repo: GraphRepository, subject: str, ops: list[NetworkOp] | None = None, extra_accounts: list[str] | None = None
) -> AnalysisResult:
    ops_set = set(ops or ALL_NETWORK_OPS)
    res = AnalysisResult()
    network: list[str] = [subject, *(extra_accounts or [])]
    if not await repo.account_summaries([subject]):
        res.notes.append(f"Account {subject} not found in the graph")
        res.network_accounts = [subject]
        return res

    # --- beneficiaries the subject registered, and transfers to them soon after
    recent_targets: list[str] = []
    recent_beneficiaries: list[BeneficiaryLink] = []
    # Risk signals only count links touching the subject or beneficiaries it explicitly registered ("core").
    # Wider-network links are recorded as evidence but don't raise risk (no guilt by distant association).
    core: set[str] = {subject}
    if "beneficiary_network" in ops_set:
        bens = await repo.beneficiaries([subject], limit=50)
        explicit = [b for b in bens if b.source == "beneficiary_added"]
        recent = explicit or bens[:5]
        recent_beneficiaries.extend(explicit)
        ben_refs: dict[str, str] = {}
        for b in recent:
            ben_refs[b.target_account] = res.add(
                EvidenceItem(
                    kind="beneficiary",
                    assertion_level=AssertionLevel.OBSERVED_RELATIONSHIP,
                    source=EvidenceSource.NEO4J,
                    source_ref={"beneficiary_id": b.beneficiary_id, "relationship": "ADDED"},
                    snapshot={
                        "owner_account": b.owner_account,
                        "target_account": b.target_account,
                        "target_customer_id": b.target_customer_id,
                        "added_at": b.added_at.isoformat(),
                        "device_id": b.device_id,
                        "ip": b.ip,
                        "how": b.source,
                    },
                    summary=f"{subject} registered {b.target_account} as a beneficiary on {b.added_at:%Y-%m-%d %H:%M}",
                )
            )
        recent_targets = list(ben_refs)
        network += recent_targets
        # transfers to a beneficiary within 24h of *explicitly* registering it. Payees inferred from a first
        # transfer satisfy this trivially, so they never count (they stay as evidence only).
        txns = await repo.transactions(subject, limit=500)
        quick = []
        for b in explicit:
            sent = [
                t
                for t in txns
                if t.dst_account == b.target_account and b.added_at <= t.ts <= b.added_at + NEW_BENEFICIARY_WINDOW
            ]
            if sent:
                quick.append((b, sum(t.amount for t in sent), [t.txn_id for t in sent]))
        quick_refs = [
            res.add(
                EvidenceItem(
                    kind="new_beneficiary_transfer",
                    assertion_level=AssertionLevel.INFERRED_PATTERN,
                    source=EvidenceSource.RULE_ENGINE,
                    source_ref={
                        "rule": "transfer_within_24h_of_adding",
                        "beneficiary_id": b.beneficiary_id,
                        "txn_ids": ids,
                    },
                    snapshot={
                        "target_account": b.target_account,
                        "amount": round(amount, 2),
                        "added_at": b.added_at.isoformat(),
                    },
                    summary=f"INR {amount:,.0f} sent to {b.target_account} within 24h of registering it",
                )
            )
            for b, amount, ids in quick
        ]
        res.signal("new_beneficiary_transfers", len(quick_refs), quick_refs)
        explicit_targets = [b.target_account for b in explicit]
        core.update(explicit_targets)
        fan_in = await repo.beneficiary_fan_in(explicit_targets) if explicit_targets else {}
        hot = {t: n for t, n in fan_in.items() if n >= 3}
        res.signal("beneficiary_fan_in", len(hot), [ben_refs[t] for t in hot], owners=hot)

    # --- money trail (aggregated flows), 2 hops out, 1 hop in
    if "money_trail" in ops_set:
        out_edges = await repo.money_trail(subject, direction="out", depth=2, min_amount=5_000, limit=40)
        in_edges = await repo.money_trail(subject, direction="in", depth=1, min_amount=5_000, limit=20)
        flow_refs = []
        for e in [*out_edges[:15], *in_edges[:10]]:
            flow_refs.append(
                res.add(
                    EvidenceItem(
                        kind="money_flow",
                        assertion_level=AssertionLevel.OBSERVED_RELATIONSHIP,
                        source=EvidenceSource.NEO4J,
                        source_ref={"src": e.src, "dst": e.dst, "relationship": "PAID"},
                        snapshot={
                            "src": e.src,
                            "dst": e.dst,
                            "count": e.count,
                            "total": round(e.total, 2),
                            "first_ts": e.first_ts.isoformat(),
                            "last_ts": e.last_ts.isoformat(),
                            "hop": e.hop,
                        },
                        summary=f"{e.src} paid {e.dst} INR {e.total:,.0f} in {e.count} transfer(s) "
                        f"({e.first_ts:%Y-%m-%d} to {e.last_ts:%Y-%m-%d}), hop {e.hop}",
                    )
                )
            )
        hop2 = {e.dst for e in out_edges if e.hop == 2}
        res.signal("onward_dispersion_accounts", len(hop2), flow_refs[:5])
        # cycle: money leaves, passes through at least one other account, and comes back
        senders = {e.src for e in in_edges if e.hop == 1}
        cycle = sorted(hop2 & senders)
        cyc_refs = []
        if cycle:
            cyc_refs.append(
                res.add(
                    EvidenceItem(
                        kind="value_cycle",
                        assertion_level=AssertionLevel.INFERRED_PATTERN,
                        source=EvidenceSource.RULE_ENGINE,
                        source_ref={"rule": "funds_return_via_intermediaries", "accounts": cycle},
                        snapshot={"accounts": cycle, "subject": subject},
                        summary=f"Funds sent by {subject} return to it via {', '.join(cycle[:5])} "
                        "(cycle of 3+ accounts)",
                    )
                )
            )
        res.signal("value_cycle_accounts", len(cycle), cyc_refs)
        network += [e.dst for e in out_edges if e.hop == 1][:5]
    network = list(dict.fromkeys(network))

    # --- shared devices: household context removes the risk weight, not the evidence
    customers_by_account: dict[str, str] = {}
    risky_links: dict[str, set[str]] = defaultdict(set)  # account -> other accounts linked w/o benign explanation
    if "shared_devices" in ops_set:
        devices = await repo.shared_devices(network, limit=30)
        dev_refs, non_household_refs, emulator_refs = [], [], []
        for d in devices[:15]:
            item, household = _device_evidence(d)
            ref = res.add(item)
            dev_refs.append(ref)
            if household or not any(a in core for u in d.users for a in u.account_ids):
                continue
            non_household_refs.append(ref)
            if d.emulator:
                emulator_refs.append(ref)
            accts = [a for u in d.users for a in u.account_ids]
            for u in d.users:
                for a in u.account_ids:
                    customers_by_account[a] = u.customer_id
                    risky_links[a].update(x for x in accts if x != a)
        linked = {x for v in risky_links.values() for x in v} - {subject}
        res.signal("shared_device_links", len(non_household_refs), non_household_refs, linked_accounts=sorted(linked))
        res.signal("emulator_devices", len(emulator_refs), emulator_refs)
        # account takeover: a beneficiary added from a device the subject first used shortly before
        subject_customer = (await repo.account_summaries([subject]))[0].customer_id
        first_seen = {
            d.device_id: u.first_seen
            for d in devices
            for u in d.users
            if u.customer_id == subject_customer and u.first_seen is not None
        }
        ato = [
            b
            for b in recent_beneficiaries
            if b.device_id in first_seen and timedelta(0) <= b.added_at - first_seen[b.device_id] <= NEW_DEVICE_WINDOW
        ]
        ato_refs = [
            ref for d, ref in zip(devices[:15], dev_refs, strict=False) if any(b.device_id == d.device_id for b in ato)
        ]
        res.signal("new_device_before_beneficiary", len(ato), ato_refs)
        res.signal(
            "household_shared_devices",
            len(dev_refs) - len(non_household_refs),
            [r for r in dev_refs if r not in non_household_refs],
        )

    if "shared_ips" in ops_set:
        ips = await repo.shared_ips(network, limit=30)
        risky_refs, infra_refs = [], []
        for ip in sorted(ips, key=lambda x: (x.kind not in RISKY_IP_KINDS, -x.user_count))[:12]:
            ref = res.add(_ip_evidence(ip))
            touches_core = any(a in core for u in ip.users for a in u.account_ids)
            if ip.kind in RISKY_IP_KINDS and touches_core:
                risky_refs.append(ref)
                accts = [a for u in ip.users for a in u.account_ids]
                for a in accts:
                    risky_links[a].update(x for x in accts if x != a)
            elif ip.is_shared_infrastructure:
                infra_refs.append(ref)
        res.signal("risky_shared_ips", len(risky_refs), risky_refs)
        res.signal("shared_infrastructure_ips", len(infra_refs), infra_refs)

    # --- beneficiaries that are linked to each other by device/IP: a coordinated cluster
    if recent_targets and risky_links:
        clustered = sorted(t for t in recent_targets if risky_links.get(t, set()) & set(recent_targets))
        refs = []
        if len(clustered) >= 2:
            refs.append(
                res.add(
                    EvidenceItem(
                        kind="beneficiary_cluster",
                        assertion_level=AssertionLevel.INFERRED_PATTERN,
                        source=EvidenceSource.RULE_ENGINE,
                        source_ref={"rule": "beneficiaries_share_identifiers", "accounts": clustered},
                        snapshot={"accounts": clustered, "of_beneficiaries": len(recent_targets)},
                        summary=f"{len(clustered)} of {len(recent_targets)} new beneficiaries are linked to each other "
                        f"through a shared device or risky IP (not explained by a household)",
                    )
                )
            )
        res.signal("beneficiaries_sharing_identifiers", len(clustered), refs, accounts=clustered)

    # --- prior investigations of anyone in the network
    if "prior_cases" in ops_set:
        cases = await repo.prior_cases(network)
        by_status: dict[str, list[str]] = defaultdict(list)
        for c in cases:
            by_status[c.status].append(res.add(_case_evidence(c)))
        res.signal("linked_confirmed_fraud_cases", len(by_status["confirmed_fraud"]), by_status["confirmed_fraud"])
        res.signal("linked_cleared_cases", len(by_status["cleared"]), by_status["cleared"])
        res.signal("linked_inconclusive_cases", len(by_status["inconclusive"]), by_status["inconclusive"])
        if not cases:
            res.notes.append("No prior investigations found for accounts in the network")

    res.network_accounts = network
    return res

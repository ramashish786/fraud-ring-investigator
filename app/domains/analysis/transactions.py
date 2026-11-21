"""Transaction investigator logic (deterministic).

Produces VERIFIED_FACT evidence for individual transactions and computed aggregates,
and INFERRED_PATTERN evidence for rule-like patterns (pass-through, structuring).
"""

from __future__ import annotations

import statistics
from datetime import timedelta

from app.core.enums import AssertionLevel, EvidenceSource
from app.domains.evidence.schemas import EvidenceItem
from app.graph.models import TxnRecord
from app.graph.repository import GraphRepository

from .signals import AnalysisResult

LARGE_TXN = 5_000.0
PASS_THROUGH_WINDOW = timedelta(hours=24)
PASS_THROUGH_RATIO = 0.8
REPORTING_THRESHOLD = 50_000.0
STRUCTURING_BAND = (0.85 * REPORTING_THRESHOLD, REPORTING_THRESHOLD)
MICRO_AUTH = 20.0
RECENT_ACCOUNT_DAYS = 60


def _txn_evidence(t: TxnRecord, account_id: str) -> EvidenceItem:
    direction = "received" if t.dst_account == account_id else "sent"
    counterpart = t.src_account if direction == "received" else (t.dst_account or t.merchant_id)
    return EvidenceItem(
        kind="transaction",
        assertion_level=AssertionLevel.VERIFIED_FACT,
        source=EvidenceSource.NEO4J,
        source_ref={"txn_id": t.txn_id},
        snapshot={
            "txn_id": t.txn_id,
            "ts": t.ts.isoformat(),
            "amount": round(t.amount, 2),
            "channel": t.channel,
            "status": t.status,
            "src_account": t.src_account,
            "dst_account": t.dst_account,
            "merchant_id": t.merchant_id,
            "device_id": t.device_id,
            "ip": t.ip,
        },
        summary=f"{t.txn_id}: {account_id} {direction} INR {t.amount:,.0f} via {t.channel}"
        f"{f' ({counterpart})' if counterpart else ''} at {t.ts:%Y-%m-%d %H:%M}",
    )


async def analyze_transactions(repo: GraphRepository, account_id: str, *, limit: int = 500) -> AnalysisResult:
    res = AnalysisResult(network_accounts=[account_id])
    all_txns = await repo.transactions(account_id, limit=limit)
    txns = [t for t in all_txns if t.status == "approved"]
    if not txns:
        res.notes.append(f"No approved transactions found for {account_id}")
        return res
    inbound = [t for t in txns if t.dst_account == account_id]
    outbound = [t for t in txns if t.src_account == account_id]
    transfers_out = [t for t in outbound if t.merchant_id is None]

    # facts: the largest movements in and out
    big_in = sorted((t for t in inbound if t.amount >= LARGE_TXN), key=lambda t: -t.amount)[:8]
    big_out = sorted((t for t in transfers_out if t.amount >= LARGE_TXN), key=lambda t: -t.amount)[:8]
    refs_in = [res.add(_txn_evidence(t, account_id)) for t in big_in]
    refs_out = [res.add(_txn_evidence(t, account_id)) for t in big_out]

    inflow, outflow = sum(t.amount for t in inbound), sum(t.amount for t in transfers_out)
    agg_ref = res.add(
        EvidenceItem(
            kind="flow_summary",
            assertion_level=AssertionLevel.VERIFIED_FACT,
            source=EvidenceSource.NEO4J,
            source_ref={"account_id": account_id, "txn_count": len(txns)},
            snapshot={
                "inflow": round(inflow, 2),
                "outflow_transfers": round(outflow, 2),
                "inbound_count": len(inbound),
                "outbound_count": len(outbound),
                "from": txns[0].ts.isoformat(),
                "to": txns[-1].ts.isoformat(),
            },
            summary=f"{account_id}: {len(inbound)} inbound (INR {inflow:,.0f}) and {len(transfers_out)} outbound "
            f"transfers (INR {outflow:,.0f}) between {txns[0].ts:%Y-%m-%d} and {txns[-1].ts:%Y-%m-%d}",
        )
    )

    # pattern: pass-through (money in, >=80% out again within 24h)
    pt_refs: list[str] = []
    for t in (t for t in inbound if t.amount >= LARGE_TXN):
        window = [o for o in transfers_out if t.ts < o.ts <= t.ts + PASS_THROUGH_WINDOW]
        forwarded = sum(o.amount for o in window)
        if forwarded >= PASS_THROUGH_RATIO * t.amount:
            hours = (max(o.ts for o in window) - t.ts).total_seconds() / 3600
            pt_refs.append(
                res.add(
                    EvidenceItem(
                        kind="pass_through",
                        assertion_level=AssertionLevel.INFERRED_PATTERN,
                        source=EvidenceSource.RULE_ENGINE,
                        source_ref={
                            "rule": "pass_through_24h",
                            "inbound_txn": t.txn_id,
                            "outbound_txns": [o.txn_id for o in window][:10],
                        },
                        snapshot={
                            "inbound_amount": round(t.amount, 2),
                            "forwarded": round(forwarded, 2),
                            "ratio": round(forwarded / t.amount, 2),
                            "hours": round(hours, 1),
                        },
                        summary=f"INR {t.amount:,.0f} received ({t.txn_id}) and {forwarded / t.amount:.0%} forwarded "
                        f"within {hours:.1f}h",
                    )
                )
            )
    res.signal("pass_through_events", len(pt_refs), pt_refs)

    # pattern: structuring (cash just under the reporting threshold)
    structured = [
        t for t in inbound if t.channel == "cash_deposit" and STRUCTURING_BAND[0] <= t.amount < STRUCTURING_BAND[1]
    ]
    st_refs = []
    if structured:
        st_refs.append(
            res.add(
                EvidenceItem(
                    kind="structuring",
                    assertion_level=AssertionLevel.INFERRED_PATTERN,
                    source=EvidenceSource.RULE_ENGINE,
                    source_ref={"rule": "cash_below_threshold", "txn_ids": [t.txn_id for t in structured][:20]},
                    snapshot={
                        "count": len(structured),
                        "threshold": REPORTING_THRESHOLD,
                        "amounts": [round(t.amount) for t in structured][:20],
                    },
                    summary=f"{len(structured)} cash deposits between INR {STRUCTURING_BAND[0]:,.0f} and "
                    f"INR {STRUCTURING_BAND[1]:,.0f}, just under the reporting threshold",
                )
            )
        )
    res.signal("structured_deposits", len(structured), st_refs)

    # velocity and amount anomaly (facts the rules threshold on)
    times = [t.ts for t in txns]
    velocity = max(sum(1 for x in times if s <= x < s + timedelta(hours=24)) for s in times)
    res.signal("velocity_24h", velocity, [agg_ref])
    amounts = [t.amount for t in txns]
    median = statistics.median(amounts)
    max_in = max((t.amount for t in inbound), default=0.0)
    ratio = max_in / median if median > 0 else 0.0
    res.signal("max_inbound_to_median", round(ratio, 1), [*refs_in[:1], agg_ref], median=round(median, 2))
    res.signal("inflow_total", round(inflow, 2), [agg_ref])

    # pattern: card testing (micro-authorisations, card not present)
    micro = [t for t in outbound if t.channel == "card_not_present" and t.amount <= MICRO_AUTH]
    declined = [
        t for t in all_txns if t.channel == "card_not_present" and t.amount <= MICRO_AUTH and t.status == "declined"
    ]
    ct_refs = []
    if micro or declined:
        ct_refs.append(
            res.add(
                EvidenceItem(
                    kind="card_testing",
                    assertion_level=AssertionLevel.INFERRED_PATTERN,
                    source=EvidenceSource.RULE_ENGINE,
                    source_ref={"rule": "micro_authorisations", "txn_ids": [t.txn_id for t in (micro + declined)][:20]},
                    snapshot={
                        "approved_micro": len(micro),
                        "declined_micro": len(declined),
                        "devices": sorted({t.device_id for t in micro + declined if t.device_id}),
                    },
                    summary=f"{len(micro) + len(declined)} card-not-present authorisations of "
                    f"INR {MICRO_AUTH:.0f} or less "
                    f"({len(declined)} declined)",
                )
            )
        )
    res.signal("card_test_authorisations", len(micro) + len(declined), ct_refs)

    # fact: account age relative to the observed activity
    summaries = await repo.account_summaries([account_id])
    summary = summaries[0] if summaries else None
    if summary and summary.opened_at:
        age_days = (txns[-1].ts - summary.opened_at).days
        age_ref = res.add(
            EvidenceItem(
                kind="account_profile",
                assertion_level=AssertionLevel.VERIFIED_FACT,
                source=EvidenceSource.NEO4J,
                source_ref={"account_id": account_id},
                snapshot={
                    "opened_at": summary.opened_at.isoformat(),
                    "age_days_at_last_activity": age_days,
                    "customer_kind": summary.customer_kind,
                    "account_type": summary.account_type,
                },
                summary=f"{account_id} opened {summary.opened_at:%Y-%m-%d} "
                f"({age_days} days before its latest activity)",
            )
        )
        res.signal("recently_opened_account", 1 if age_days <= RECENT_ACCOUNT_DAYS else 0, [age_ref], age_days=age_days)
    res.signal("outflow_total", round(outflow, 2), [agg_ref, *refs_out[:3]])
    if len(txns) >= limit:
        res.notes.append(f"Transaction history truncated at {limit} rows")
    return res

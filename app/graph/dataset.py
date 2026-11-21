"""Reads the simulator export (our stand-in for a core-banking extract) and derives the
aggregated relationships of the graph model (D-002, D-018).

Both the Neo4j loader and the in-memory repository consume this one class, so the
derived edges (USES, LOGGED_IN_FROM, ACCESSED_FROM, PAID) are computed identically
for production and tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


def _read(path: Path, ts_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    for c in ts_cols:
        df[c] = pd.to_datetime(df[c], format="ISO8601", utc=True)
    return df


def _none(v: Any) -> Any:
    """NaN/NaT -> None; numpy scalars -> Python scalars (the Neo4j driver rejects numpy types)."""
    if v is pd.NaT or (isinstance(v, float) and v != v):
        return None
    if hasattr(v, "item") and not isinstance(v, str):
        v = v.item()
        return None if isinstance(v, float) and v != v else v
    return v


@dataclass
class BankDataset:
    customers: pd.DataFrame
    accounts: pd.DataFrame
    devices: pd.DataFrame
    ips: pd.DataFrame
    merchants: pd.DataFrame
    transactions: pd.DataFrame
    beneficiaries: pd.DataFrame
    fraud_cases: list[dict[str, Any]]
    uses: pd.DataFrame  # customer_id, device_id, first_seen, last_seen, observations, source_ref
    logged_in_from: pd.DataFrame  # customer_id, ip, first_seen, last_seen, observations, failed
    accessed_from: pd.DataFrame  # device_id, ip, first_seen, last_seen, observations
    paid: pd.DataFrame  # src_account, dst_account, count, total, first_ts, last_ts

    @classmethod
    def load(cls, root: str | Path) -> BankDataset:
        d = Path(root)
        customers = _read(d / "entities/customers.csv", ("created_at",))
        accounts = _read(d / "entities/accounts.csv", ("opened_at",))
        txns = _read(d / "events/transactions.csv", ("ts",))
        txns = txns.drop(columns=[c for c in ("is_fraud", "ring_id", "pattern") if c in txns])  # never load labels
        logins = _read(d / "events/logins.csv", ("ts",))
        bens = _read(d / "entities/beneficiaries.csv", ("added_at",))
        cases_path = d / "cases/fraud_cases.json"
        cases = json.loads(cases_path.read_text()) if cases_path.exists() else []

        owner = dict(zip(accounts.account_id, accounts.customer_id, strict=True))
        ok = logins[logins.success.astype(bool)]
        login_uses = ok[["customer_id", "device_id", "ts"]].assign(source_ref="logins")
        txn_dev = txns[txns.device_id.notna() & txns.src_account.notna()]
        txn_uses = pd.DataFrame(
            {
                "customer_id": txn_dev.src_account.map(owner),
                "device_id": txn_dev.device_id,
                "ts": txn_dev.ts,
                "source_ref": "transactions",
            }
        )
        all_uses = pd.concat([login_uses, txn_uses], ignore_index=True)
        uses = (
            all_uses.groupby(["customer_id", "device_id"])
            .agg(
                first_seen=("ts", "min"),
                last_seen=("ts", "max"),
                observations=("ts", "size"),
                source_ref=("source_ref", lambda s: ",".join(sorted(set(s)))),
            )
            .reset_index()
        )
        logged_in = (
            logins.groupby(["customer_id", "ip"])
            .agg(
                first_seen=("ts", "min"),
                last_seen=("ts", "max"),
                observations=("ts", "size"),
                failed=("success", lambda s: int((~s.astype(bool)).sum())),
            )
            .reset_index()
        )
        accessed = (
            logins.groupby(["device_id", "ip"])
            .agg(first_seen=("ts", "min"), last_seen=("ts", "max"), observations=("ts", "size"))
            .reset_index()
        )
        p2p = txns[txns.src_account.notna() & txns.dst_account.notna() & (txns.status == "approved")]
        paid = (
            p2p.groupby(["src_account", "dst_account"])
            .agg(count=("amount", "size"), total=("amount", "sum"), first_ts=("ts", "min"), last_ts=("ts", "max"))
            .reset_index()
        )
        return cls(
            customers=customers,
            accounts=accounts,
            devices=_read(d / "entities/devices.csv"),
            ips=_read(d / "entities/ips.csv"),
            merchants=_read(d / "entities/merchants.csv"),
            transactions=txns,
            beneficiaries=bens,
            fraud_cases=cases,
            uses=uses,
            logged_in_from=logged_in,
            accessed_from=accessed,
            paid=paid,
        )

    @staticmethod
    def records(df: pd.DataFrame) -> list[dict[str, Any]]:
        """DataFrame -> JSON-friendly dicts (timestamps as ISO strings, NaN as None)."""
        out = []
        for rec in df.to_dict("records"):
            out.append({k: (v.isoformat() if isinstance(v, pd.Timestamp) else _none(v)) for k, v in rec.items()})
        return out

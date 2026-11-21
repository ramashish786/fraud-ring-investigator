"""In-memory GraphRepository built from a BankDataset (D-026).

A test double with the same semantics as the Cypher catalog, verified by the shared
contract tests. Used for unit/e2e tests and offline evaluation where Neo4j isn't
available. It is not a production store.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

import pandas as pd

from .dataset import BankDataset
from .models import (
    MAX_USERS_PER_IDENTIFIER,
    AccountSummary,
    BeneficiaryLink,
    CaseLink,
    IdentifierUse,
    PaidEdge,
    SharedDevice,
    SharedIP,
    TxnRecord,
    clamp_depth,
    clamp_limit,
)

_PATH_LIMIT = 2000


def _dt(v: Any) -> datetime | None:
    if v is None or v is pd.NaT or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, pd.Timestamp):
        out: datetime = v.to_pydatetime()
        return out
    if isinstance(v, str):
        parsed: datetime = pd.Timestamp(v).to_pydatetime()
        return parsed
    return v if isinstance(v, datetime) else None


class InMemoryGraphRepository:
    def __init__(self, ds: BankDataset) -> None:
        self.customers = {r["customer_id"]: r for r in ds.customers.to_dict("records")}
        self.accounts = {r["account_id"]: r for r in ds.accounts.to_dict("records")}
        self.cust_accounts: dict[str, list[str]] = defaultdict(list)
        for a, r in self.accounts.items():
            self.cust_accounts[r["customer_id"]].append(a)
        self.devices = {r["device_id"]: r for r in ds.devices.to_dict("records")}
        self.ips = {r["ip"]: r for r in ds.ips.to_dict("records")}

        self.txns = ds.transactions.sort_values("ts").to_dict("records")
        self.txn_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in self.txns:
            for key in ("src_account", "dst_account"):
                a = t.get(key)
                if isinstance(a, str):
                    self.txn_by_account[a].append(t)

        self.paid_out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.paid_in: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in ds.paid.to_dict("records"):
            if e["src_account"] in self.accounts and e["dst_account"] in self.accounts:
                self.paid_out[e["src_account"]].append(e)
                self.paid_in[e["dst_account"]].append(e)

        self.device_users: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.cust_devices: dict[str, set[str]] = defaultdict(set)
        for u in ds.uses.to_dict("records"):
            if u["customer_id"] in self.customers and u["device_id"] in self.devices:
                self.device_users[u["device_id"]][u["customer_id"]] = u
                self.cust_devices[u["customer_id"]].add(u["device_id"])
        self.ip_users: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.cust_ips: dict[str, set[str]] = defaultdict(set)
        for u in ds.logged_in_from.to_dict("records"):
            if u["customer_id"] in self.customers and u["ip"] in self.ips:
                self.ip_users[u["ip"]][u["customer_id"]] = {**u, "source_ref": "logins"}
                self.cust_ips[u["customer_id"]].add(u["ip"])
        self.device_ip_kinds: dict[str, set[str]] = defaultdict(set)
        for r in ds.accessed_from.to_dict("records"):
            if r["ip"] in self.ips:
                self.device_ip_kinds[r["device_id"]].add(self.ips[r["ip"]]["kind"])

        self.bens_by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.ben_owners_by_target: dict[str, set[str]] = defaultdict(set)
        for b in ds.beneficiaries.to_dict("records"):
            if b["owner_account"] in self.accounts and b["target_account"] in self.accounts:
                self.bens_by_owner[b["owner_account"]].append(b)
                self.ben_owners_by_target[b["target_account"]].add(b["owner_account"])

        self.cases_by_account: dict[str, list[CaseLink]] = defaultdict(list)
        for c in ds.fraud_cases:
            for a in c["accounts"]:
                if a["account_id"] in self.accounts:
                    self.cases_by_account[a["account_id"]].append(
                        CaseLink(
                            case_id=c["case_id"],
                            typology=c["typology"],
                            status=c["status"],
                            role=a["role"],
                            account_id=a["account_id"],
                            closed_at=_dt(c.get("closed_at")),
                            summary=c.get("summary"),
                        )
                    )
        self.investigations: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ helpers
    def _owners(self, account_ids: list[str]) -> set[str]:
        return {self.accounts[a]["customer_id"] for a in account_ids if a in self.accounts}

    def _use(self, customer_id: str, u: dict[str, Any]) -> IdentifierUse:
        return IdentifierUse(
            customer_id=customer_id,
            account_ids=sorted(self.cust_accounts.get(customer_id, [])),
            first_seen=_dt(u["first_seen"]),
            last_seen=_dt(u["last_seen"]),
            observations=int(u["observations"]),
            address_key=self.customers[customer_id].get("address_key"),
            source_ref=u.get("source_ref"),
        )

    # ------------------------------------------------------------------ protocol
    async def account_summaries(self, account_ids: list[str]) -> list[AccountSummary]:
        out = []
        for a in account_ids:
            if a in self.accounts:
                acct = self.accounts[a]
                cust = self.customers[acct["customer_id"]]
                out.append(
                    AccountSummary(
                        account_id=a,
                        customer_id=acct["customer_id"],
                        customer_kind=cust.get("kind"),
                        account_type=acct.get("type"),
                        opened_at=_dt(acct.get("opened_at")),
                        customer_created_at=_dt(cust.get("created_at")),
                        address_key=cust.get("address_key"),
                        city=cust.get("city"),
                    )
                )
        return out

    async def transactions(
        self, account_id: str, *, since: datetime | None = None, until: datetime | None = None, limit: int = 200
    ) -> list[TxnRecord]:
        out = []
        for t in self.txn_by_account.get(account_id, []):
            ts = _dt(t["ts"])
            assert ts is not None
            if (since and ts < since) or (until and ts > until):
                continue
            out.append(
                TxnRecord(
                    txn_id=t["txn_id"],
                    ts=ts,
                    amount=float(t["amount"]),
                    channel=t["channel"],
                    status=t["status"],
                    **{
                        k: (t[k] if isinstance(t.get(k), str) else None)
                        for k in ("src_account", "dst_account", "merchant_id", "device_id", "ip")
                    },
                )
            )
            if len(out) >= clamp_limit(limit):
                break
        return out

    async def money_trail(
        self,
        account_id: str,
        *,
        direction: Literal["out", "in"],
        depth: int = 2,
        min_amount: float = 0.0,
        limit: int = 200,
    ) -> list[PaidEdge]:
        depth = clamp_depth(depth)
        adj = self.paid_out if direction == "out" else self.paid_in
        nxt = "dst_account" if direction == "out" else "src_account"
        hops: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        paths = 0

        def dfs(node: str, level: int, used: set[tuple[str, str]], path: list[dict[str, Any]]) -> None:
            nonlocal paths
            if paths >= _PATH_LIMIT or level > depth:
                return
            for e in adj.get(node, []):
                key = (e["src_account"], e["dst_account"])
                if key in used or e["total"] < min_amount:
                    continue
                new_path = [*path, e]
                end = e[nxt]
                if end != account_id:  # a path counts only if it ends somewhere other than the start
                    paths += 1
                    for i, pe in enumerate(new_path):
                        k = (pe["src_account"], pe["dst_account"])
                        if k not in hops or hops[k][0] > i + 1:
                            hops[k] = (i + 1, pe)
                if level < depth:
                    dfs(end, level + 1, used | {key}, new_path)

        dfs(account_id, 1, set(), [])
        edges = [
            PaidEdge(
                src=e["src_account"],
                dst=e["dst_account"],
                count=int(e["count"]),
                total=float(e["total"]),
                first_ts=_dt(e["first_ts"]),
                last_ts=_dt(e["last_ts"]),
                hop=h,
            )  # type: ignore[arg-type,unused-ignore]
            for h, e in hops.values()
        ]
        edges.sort(key=lambda x: (x.hop, -x.total))
        return edges[: clamp_limit(limit)]

    async def shared_devices(self, account_ids: list[str], *, limit: int = 50) -> list[SharedDevice]:
        devices = {d for c in self._owners(account_ids) for d in self.cust_devices.get(c, set())}
        out = []
        for d in devices:
            users = self.device_users[d]
            if len(users) < 2:
                continue
            dev = self.devices[d]
            out.append(
                SharedDevice(
                    device_id=d,
                    emulator=bool(dev.get("emulator")),
                    os=dev.get("os"),
                    user_count=len(users),
                    users=[self._use(c, u) for c, u in sorted(users.items())][:MAX_USERS_PER_IDENTIFIER],
                    ip_kinds=sorted(self.device_ip_kinds.get(d, set())),
                )
            )
        out.sort(key=lambda x: (-x.user_count, x.device_id))
        return out[: clamp_limit(limit)]

    async def shared_ips(self, account_ids: list[str], *, limit: int = 50) -> list[SharedIP]:
        ips = {i for c in self._owners(account_ids) for i in self.cust_ips.get(c, set())}
        out = []
        for i in ips:
            users = self.ip_users[i]
            if len(users) < 2:
                continue
            meta = self.ips[i]
            out.append(
                SharedIP(
                    ip=i,
                    kind=meta["kind"],
                    country=meta.get("country"),
                    user_count=len(users),
                    users=[self._use(c, u) for c, u in sorted(users.items())][:MAX_USERS_PER_IDENTIFIER],
                )
            )
        out.sort(key=lambda x: (-x.user_count, x.ip))
        return out[: clamp_limit(limit)]

    async def beneficiaries(self, account_ids: list[str], *, limit: int = 100) -> list[BeneficiaryLink]:
        rows = [b for a in account_ids for b in self.bens_by_owner.get(a, [])]
        out = [
            BeneficiaryLink(
                beneficiary_id=b["beneficiary_id"],
                owner_account=b["owner_account"],
                target_account=b["target_account"],
                target_customer_id=self.accounts[b["target_account"]]["customer_id"],
                added_at=_dt(b["added_at"]),  # type: ignore[arg-type,unused-ignore]
                device_id=b["device_id"] if isinstance(b.get("device_id"), str) else None,
                ip=b["ip"] if isinstance(b.get("ip"), str) else None,
                source=b.get("source"),
            )
            for b in rows
        ]
        out.sort(key=lambda x: (-x.added_at.timestamp(), x.beneficiary_id))
        return out[: clamp_limit(limit)]

    async def beneficiary_fan_in(self, target_accounts: list[str]) -> dict[str, int]:
        return {t: len(self.ben_owners_by_target.get(t, set())) for t in target_accounts}

    async def prior_cases(self, account_ids: list[str]) -> list[CaseLink]:
        out = [c for a in account_ids for c in self.cases_by_account.get(a, [])]
        out.sort(key=lambda c: c.closed_at.timestamp() if c.closed_at else 0, reverse=True)
        return out

    async def upsert_investigation(self, investigation_id: str, status: str, account_ids: list[str]) -> None:
        self.investigations[investigation_id] = {"status": status, "account_ids": sorted(set(account_ids))}

"""Bulk-load a BankDataset into Neo4j (seed path, not a request path).

    python -m app.graph.loader --data data [--wipe]

* UNWIND batches inside managed write transactions (automatic retry of transient errors).
* MERGE on constrained keys -> re-running is idempotent.
* Aggregated edges carry provenance: first/last seen, observation counts, source_ref,
  confidence and verification_status (D-018).
* Ground-truth labels are never loaded (BankDataset drops them).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import Any, LiteralString

from app.core.settings import get_settings
from app.infrastructure.neo4j.client import Neo4jClient
from app.infrastructure.observability.logging import configure_logging, get_logger

from .dataset import BankDataset
from .schema.bootstrap import apply_schema

log = get_logger("graph.loader")

Q: dict[str, LiteralString] = {
    "customers": """
UNWIND $rows AS r MERGE (c:Customer {customer_id: r.customer_id})
SET c.kind = r.kind, c.name = r.name, c.city = r.city, c.address_key = r.address_key,
    c.created_at = datetime(r.created_at)""",
    "accounts": """
UNWIND $rows AS r MATCH (c:Customer {customer_id: r.customer_id})
MERGE (a:Account {account_id: r.account_id}) SET a.type = r.type, a.opened_at = datetime(r.opened_at)
MERGE (c)-[o:OWNS]->(a) SET o.source_ref = 'accounts'""",
    "devices": "UNWIND $rows AS r MERGE (d:Device {device_id: r.device_id}) SET d.os = r.os, d.emulator = r.emulator",
    "ips": """UNWIND $rows AS r MERGE (i:IPAddress {ip: r.ip})
SET i.kind = r.kind, i.city = r.city, i.country = r.country""",
    "merchants": """UNWIND $rows AS r MERGE (m:Merchant {merchant_id: r.merchant_id})
SET m.category = r.category, m.risk = r.risk""",
    "uses": """
UNWIND $rows AS r MATCH (c:Customer {customer_id: r.customer_id}), (d:Device {device_id: r.device_id})
MERGE (c)-[u:USES]->(d)
SET u.first_seen = datetime(r.first_seen), u.last_seen = datetime(r.last_seen), u.observations = r.observations,
    u.source_ref = r.source_ref, u.confidence = 1.0, u.verification_status = 'observed'""",
    "logged_in_from": """
UNWIND $rows AS r MATCH (c:Customer {customer_id: r.customer_id}), (i:IPAddress {ip: r.ip})
MERGE (c)-[u:LOGGED_IN_FROM]->(i)
SET u.first_seen = datetime(r.first_seen), u.last_seen = datetime(r.last_seen), u.observations = r.observations,
    u.failed = r.failed, u.source_ref = 'logins', u.confidence = 1.0, u.verification_status = 'observed'""",
    "accessed_from": """
UNWIND $rows AS r MATCH (d:Device {device_id: r.device_id}), (i:IPAddress {ip: r.ip})
MERGE (d)-[u:ACCESSED_FROM]->(i)
SET u.first_seen = datetime(r.first_seen), u.last_seen = datetime(r.last_seen), u.observations = r.observations,
    u.source_ref = 'logins'""",
    "transactions": """
UNWIND $rows AS r
MERGE (t:Transaction {txn_id: r.txn_id})
SET t.ts = datetime(r.ts), t.amount = r.amount, t.currency = r.currency, t.channel = r.channel, t.status = r.status
WITH t, r
OPTIONAL MATCH (s:Account {account_id: r.src_account})
FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END | MERGE (s)-[:SENT]->(t))
WITH t, r
OPTIONAL MATCH (d:Account {account_id: r.dst_account})
FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (t)-[:RECEIVED_BY]->(d))
WITH t, r
OPTIONAL MATCH (m:Merchant {merchant_id: r.merchant_id})
FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | MERGE (t)-[:AT]->(m))
WITH t, r
OPTIONAL MATCH (dv:Device {device_id: r.device_id})
FOREACH (_ IN CASE WHEN dv IS NULL THEN [] ELSE [1] END | MERGE (t)-[:VIA_DEVICE]->(dv))
WITH t, r
OPTIONAL MATCH (i:IPAddress {ip: r.ip})
FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | MERGE (t)-[:FROM_IP]->(i))""",
    "paid": """
UNWIND $rows AS r MATCH (s:Account {account_id: r.src_account}), (d:Account {account_id: r.dst_account})
MERGE (s)-[p:PAID]->(d)
SET p.count = r.count, p.total = r.total, p.first_ts = datetime(r.first_ts), p.last_ts = datetime(r.last_ts)""",
    "beneficiaries": """
UNWIND $rows AS r MATCH (o:Account {account_id: r.owner_account}), (t:Account {account_id: r.target_account})
MERGE (b:Beneficiary {beneficiary_id: r.beneficiary_id})
MERGE (o)-[a:ADDED]->(b)
SET a.ts = datetime(r.added_at), a.device_id = r.device_id, a.ip = r.ip, a.source = r.source,
    a.source_ref = 'beneficiaries'
MERGE (b)-[:LINKED_TO]->(t)""",
    "fraud_cases": """
UNWIND $rows AS r MERGE (f:FraudCase {case_id: r.case_id})
SET f.typology = r.typology, f.status = r.status, f.summary = r.summary,
    f.opened_at = datetime(r.opened_at), f.closed_at = datetime(r.closed_at)
WITH f, r UNWIND r.accounts AS link
MATCH (a:Account {account_id: link.account_id})
MERGE (a)-[i:INVOLVED_IN]->(f)
SET i.role = link.role, i.verification_status = r.status, i.source_ref = 'fraud_cases'""",
}

WIPE: LiteralString = (
    "MATCH (n) WHERE NOT n:Document AND NOT n:Chunk CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS"
)


async def load(client: Neo4jClient, ds: BankDataset, batch: int = 2000) -> dict[str, int]:
    counts: dict[str, int] = {}
    plan: list[tuple[str, list[dict[str, Any]]]] = [
        ("customers", ds.records(ds.customers[["customer_id", "kind", "name", "city", "address_key", "created_at"]])),
        ("accounts", ds.records(ds.accounts[["account_id", "customer_id", "type", "opened_at"]])),
        ("devices", ds.records(ds.devices)),
        ("ips", ds.records(ds.ips)),
        ("merchants", ds.records(ds.merchants)),
        ("uses", ds.records(ds.uses)),
        ("logged_in_from", ds.records(ds.logged_in_from)),
        ("accessed_from", ds.records(ds.accessed_from)),
        ("transactions", ds.records(ds.transactions)),
        ("paid", ds.records(ds.paid)),
        ("beneficiaries", ds.records(ds.beneficiaries)),
        ("fraud_cases", ds.fraud_cases),
    ]
    for name, rows in plan:
        start = time.perf_counter()
        for i in range(0, len(rows), batch):
            await client.write(Q[name], {"rows": rows[i : i + batch]})
        counts[name] = len(rows)
        log.info("graph.loaded", entity=name, rows=len(rows), seconds=round(time.perf_counter() - start, 1))
    return counts


async def main() -> None:
    p = argparse.ArgumentParser(description="Load the simulator export into Neo4j")
    p.add_argument("--data", default="data")
    p.add_argument("--wipe", action="store_true", help="delete existing banking graph first (keeps documents)")
    args = p.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    client = Neo4jClient.from_settings(settings)
    try:
        await apply_schema(client, settings.embedding_dimensions)
        if args.wipe:
            await client.run_schema(WIPE)  # CALL IN TRANSACTIONS requires an implicit transaction
        await load(client, BankDataset.load(args.data))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

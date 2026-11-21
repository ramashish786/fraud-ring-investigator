"""Investigation network view for the UI, built from the investigation's *evidence snapshots*.

Building from evidence (not a live Neo4j query) means the picture the analyst sees is
exactly what the report cites, and it stays stable if the graph is reloaded (D-005).
Every edge carries its evidence ref, assertion level and any benign context.
"""

from __future__ import annotations

from typing import Any

from app.domains.identity.actor import Actor
from app.graph.models import GraphEdge, GraphNode, Subgraph

from .models import Evidence
from .service import redact

_MAX_NODES = 300


def build_graph(subject_account: str, evidence: list[Evidence], actor: Actor) -> Subgraph:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    def node(nid: str, label: str, **props: Any) -> str:
        if nid not in nodes:
            nodes[nid] = GraphNode(id=nid, label=label, properties={"subject": nid == subject_account, **props})
        return nid

    def edge(src: str, dst: str, type_: str, e: Evidence, **props: Any) -> None:
        edges.append(
            GraphEdge(
                source=src,
                target=dst,
                type=type_,
                properties={"evidence_ref": e.ref, "assertion_level": str(e.assertion_level), **props},
            )
        )

    node(subject_account, "Account")
    for raw in evidence:
        e = raw
        if redact(raw, actor).redacted:
            continue
        snap = e.snapshot
        if e.kind == "shared_device":
            d = node(
                f"device:{snap['device_id']}",
                "Device",
                name=snap["device_id"],
                emulator=snap.get("emulator"),
                benign_context=snap.get("benign_context"),
            )
            for u in snap.get("users", []):
                for a in u.get("account_ids", [])[:3]:
                    edge(node(a, "Account"), d, "USES", e, benign_context=snap.get("benign_context"))
        elif e.kind == "shared_ip":
            i = node(
                f"ip:{snap['ip']}",
                "IPAddress",
                name=snap["ip"],
                kind=snap.get("kind"),
                benign_context=snap.get("benign_context"),
            )
            # shared networks (carrier NAT, wifi) are context, not identity links: draw only a few users
            cap = 3 if snap.get("benign_context") else 12
            for u in snap.get("users", [])[:cap]:
                for a in u.get("account_ids", [])[:2]:
                    edge(node(a, "Account"), i, "LOGGED_IN_FROM", e, benign_context=snap.get("benign_context"))
        elif e.kind == "beneficiary":
            edge(
                node(snap["owner_account"], "Account"),
                node(snap["target_account"], "Account"),
                "ADDED_BENEFICIARY",
                e,
                added_at=snap.get("added_at"),
            )
        elif e.kind == "money_flow":
            edge(
                node(snap["src"], "Account"),
                node(snap["dst"], "Account"),
                "PAID",
                e,
                total=snap.get("total"),
                count=snap.get("count"),
            )
        elif e.kind == "prior_case":
            c = node(
                f"case:{snap['case_id']}",
                "FraudCase",
                name=snap["case_id"],
                status=snap.get("status"),
                typology=snap.get("typology"),
            )
            edge(node(e.source_ref["account_id"], "Account"), c, "INVOLVED_IN", e, status=snap.get("status"))
        if len(nodes) >= _MAX_NODES:
            return Subgraph(nodes=list(nodes.values()), edges=edges, truncated=True)
    return Subgraph(nodes=list(nodes.values()), edges=edges)

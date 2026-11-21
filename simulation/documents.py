"""Synthetic document corpus for RAG (spec §7) plus a labelled retrieval evaluation set.

    python -m simulation.documents --data data

Writes data/documents/<doc_type>/<slug>.md, data/documents/manifest.json and
data/documents/retrieval_eval.json (query -> relevant document keys). All content is
synthetic: no real institutions, customers or regulations are reproduced.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TYPOLOGY_TITLES = {
    "money_mule": "Money mule activity",
    "account_takeover": "Account takeover",
    "synthetic_identity": "Synthetic identity bust-out",
    "card_testing": "Card testing (enumeration)",
    "round_tripping": "Round-tripping between related firms",
    "shared_device": "Shared device alert",
    "beneficiary_fan_in": "Beneficiary fan-in",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def case_report(c: dict[str, Any]) -> str:
    accounts = "\n".join(f"- {a['account_id']} ({a['role']})" for a in c["accounts"])
    facts = "\n".join(f"- {f}" for f in c["key_facts"])
    outcome = {"confirmed_fraud": "Confirmed fraud", "cleared": "Cleared (no fraud)", "inconclusive": "Inconclusive"}[
        c["status"]
    ]
    lesson = {
        "confirmed_fraud": "Similar combinations of rapid onward transfers, new beneficiaries and shared access "
        "infrastructure should be escalated quickly; individually each signal was weak.",
        "cleared": "The triggering signal had a legitimate explanation. Check household, merchant or device-change "
        "context before escalating similar alerts.",
        "inconclusive": "Evidence did not establish intent. Re-open if the account is linked to new confirmed cases.",
    }[c["status"]]
    return f"""# Case report {c["case_id"]}: {TYPOLOGY_TITLES.get(c["typology"], c["typology"])}

Case ID: {c["case_id"]}
Typology: {c["typology"]}
Opened: {c["opened_at"][:10]}  Closed: {c["closed_at"][:10]}
Outcome: {outcome}

## Summary
{c["summary"]}

## Accounts involved
{accounts}

## Key facts
{facts}

## Outcome
{outcome}. {c["outcome_reason"]}

## Lessons for future investigations
{lesson}
"""


GUIDANCE: list[dict[str, Any]] = [
    {
        "key": "policy-aml-monitoring",
        "title": "Transaction monitoring policy",
        "doc_type": "policy",
        "classification": "internal",
        "body": """
## Purpose
Defines how alerts from automated transaction monitoring are triaged and investigated.

## Requirements
1. Every high or critical alert must be investigated within 2 business days.
2. Investigators must document the evidence for every conclusion. Indicators such as a shared device or a shared IP
   address are not proof of fraud on their own and must be weighed against legitimate explanations.
3. Cash deposits structured just below the reporting threshold must be reviewed together, not one by one.
4. When funds are received and forwarded within 24 hours, the investigator must trace at least two hops of onward
   transfers.
5. A suspicious activity report must be considered when the evidence supports coordinated activity.
""",
    },
    {
        "key": "policy-money-mules",
        "title": "Money mule risk policy",
        "doc_type": "policy",
        "classification": "internal",
        "body": """
## Definition
A money mule receives and moves funds on behalf of others, often recruited through fake job offers.

## Red flags
- Inflows from several unrelated senders followed by rapid outflows (pass-through).
- New beneficiaries added shortly before large transfers.
- Beneficiaries that share devices or IP addresses with each other.
- Cash withdrawals at ATMs soon after receipt.

## Required actions
Restrict outbound payments pending review when an account is linked to a confirmed mule case and shows pass-through
behaviour. Escalate to the financial intelligence team when three or more linked accounts are involved.
""",
    },
    {
        "key": "procedure-investigation",
        "title": "Fraud investigation procedure",
        "doc_type": "procedure",
        "classification": "internal",
        "body": """
## Steps
1. Confirm the alert facts from the system of record (transactions, logins).
2. Map relationships: shared devices, IP addresses, beneficiaries and money trails.
3. Search historical cases for similar patterns and note both similarities and differences.
4. Check applicable policies.
5. Record supporting evidence, contradicting evidence and missing information separately.
6. A human reviewer approves or rejects the conclusion. Automated scores are indicators, not decisions.

## Evidence standards
Distinguish verified facts, observed relationships, inferred patterns, hypotheses and confirmed outcomes.
""",
    },
    {
        "key": "procedure-escalation-sar",
        "title": "Escalation and suspicious activity reporting",
        "doc_type": "procedure",
        "classification": "confidential",
        "body": """
## When to escalate
Escalate to the financial intelligence unit when an investigation finds coordinated movement of funds across three or
more accounts, links to a confirmed prior case, or structuring around the reporting threshold.

## Filing
Prepare the suspicious activity report within 7 days of the escalation decision. Do not inform the customer
(tipping-off is prohibited).
""",
    },
    {
        "key": "procedure-law-enforcement",
        "title": "Law enforcement referral protocol",
        "doc_type": "procedure",
        "classification": "restricted",
        "body": """
## Scope
Applies when a confirmed fraud ring involves account takeover victims or more than INR 10 lakh in losses.

## Protocol
Only the head of financial crime may approve a referral. Preserve device and login evidence for 7 years.
""",
    },
    {
        "key": "guideline-thresholds",
        "title": "Monitoring thresholds and rules guideline",
        "doc_type": "monitoring_guideline",
        "classification": "internal",
        "body": """
## Thresholds
- Cash deposits between INR 42,500 and INR 50,000: possible structuring.
- 80% or more of an inflow forwarded within 24 hours: pass-through.
- A device used by more than 4 customers who do not share an address: shared-device risk.
- IP addresses of carrier-grade NAT, public wifi or corporate networks are shared by many customers and must not be
  treated as identity links.
""",
    },
    {
        "key": "pattern-shared-device-benign",
        "title": "Legitimate reasons for shared devices and IPs",
        "doc_type": "fraud_pattern",
        "classification": "public",
        "body": """
## Households
Family members often use one tablet or phone and share a home IP. They have the same registered address and make
small, two-way transfers.

## Shared networks
Mobile carriers place thousands of customers behind the same public IP (carrier-grade NAT). Public wifi and office
networks do the same. A shared IP of these types is not evidence of a relationship.

## Device changes
Customers who buy a new phone log in from a new device, reset their password and often make a large purchase.
""",
    },
    {
        "key": "pattern-mule-network",
        "title": "Typology: layered mule networks",
        "doc_type": "fraud_pattern",
        "classification": "public",
        "body": """
## Pattern
Stolen or criminal funds enter through account takeover drains or structured cash deposits, move through one or two
tiers of mule accounts within hours, and leave through ATM withdrawals. Mules keep a small commission (2-5%).

## Graph signature
A controller device logs into several mule accounts. Beneficiaries of the collecting account are linked to each other
through a shared device or VPN address.
""",
    },
    {
        "key": "pattern-account-takeover",
        "title": "Typology: account takeover",
        "doc_type": "fraud_pattern",
        "classification": "public",
        "body": """
## Pattern
Failed logins from datacenter or VPN addresses (credential stuffing), then a successful login from a new device, a
password reset, a newly added beneficiary and an immediate transfer of most of the balance.
""",
    },
    {
        "key": "pattern-synthetic-identity",
        "title": "Typology: synthetic identity bust-out",
        "doc_type": "fraud_pattern",
        "classification": "public",
        "body": """
## Pattern
Several new identities reuse phone numbers and addresses, behave normally for weeks, then all spend heavily on
resellable goods (electronics, gift cards, jewellery) on the same day.
""",
    },
]

RETRIEVAL_QUERIES: list[dict[str, Any]] = [
    {
        "query": "account forwards funds to newly added beneficiaries who share a device and VPN address",
        "group": "guidance",
        "relevant": ["policy-money-mules", "pattern-mule-network"],
    },
    {
        "query": "is a shared IP address from a mobile carrier evidence of a relationship between customers",
        "group": "guidance",
        "relevant": ["pattern-shared-device-benign", "guideline-thresholds"],
    },
    {
        "query": "cash deposits just below the reporting threshold",
        "group": "guidance",
        "relevant": ["guideline-thresholds", "policy-aml-monitoring"],
    },
    {
        "query": "when must an investigation be escalated and a suspicious activity report filed",
        "group": "guidance",
        "relevant": ["procedure-escalation-sar", "policy-aml-monitoring"],
    },
    {
        "query": "new device login after failed logins then password reset and transfer to new beneficiary",
        "group": "guidance",
        "relevant": ["pattern-account-takeover"],
    },
    {
        "query": "family members using the same tablet flagged as shared device",
        "group": "case",
        "relevant_typology_status": ["shared_device:cleared"],
    },
    {
        "query": "funds received from unrelated senders and forwarded within hours to cash-out accounts",
        "group": "case",
        "relevant_typology_status": ["money_mule:confirmed_fraud"],
    },
    {
        "query": "merchant collection account receiving many small payments",
        "group": "case",
        "relevant_typology_status": ["beneficiary_fan_in:cleared"],
    },
    {
        "query": "customer bought a new phone and reset password before a large purchase",
        "group": "case",
        "relevant_typology_status": ["account_takeover:cleared"],
    },
    {
        "query": "identities sharing phone numbers maxed out on electronics on the same day",
        "group": "case",
        "relevant_typology_status": ["synthetic_identity:confirmed_fraud"],
    },
]


def generate(data: Path) -> list[dict[str, Any]]:
    out = data / "documents"
    cases = json.loads((data / "cases/fraud_cases.json").read_text())
    manifest: list[dict[str, Any]] = []
    for c in cases:
        key = f"case-{c['case_id'].lower()}"
        path = out / "case_report" / f"{key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case_report(c))
        manifest.append(
            {
                "key": key,
                "title": f"Case report {c['case_id']}",
                "doc_type": "case_report",
                "classification": "confidential",
                "case_id": c["case_id"],
                "source": "case-management-export",
                "path": str(path.relative_to(data)),
                "typology": c["typology"],
                "status": c["status"],
            }
        )
    for g in GUIDANCE:
        path = out / g["doc_type"] / f"{g['key']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {g['title']}\n{g['body']}")
        manifest.append(
            {
                "key": g["key"],
                "title": g["title"],
                "doc_type": g["doc_type"],
                "classification": g["classification"],
                "case_id": None,
                "source": "policy-library",
                "path": str(path.relative_to(data)),
            }
        )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    evalset = []
    for q in RETRIEVAL_QUERIES:
        rel = q.get("relevant") or [
            m["key"]
            for m in manifest
            if m["doc_type"] == "case_report" and f"{m['typology']}:{m['status']}" in q["relevant_typology_status"]
        ]
        evalset.append({"query": q["query"], "group": q["group"], "relevant": rel})
    (out / "retrieval_eval.json").write_text(json.dumps(evalset, indent=2))
    return manifest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    m = generate(Path(p.parse_args().data))
    print(f"wrote {len(m)} documents")


if __name__ == "__main__":
    main()

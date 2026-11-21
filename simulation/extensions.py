"""Phase 2 simulator extensions (D-015).

* Beneficiaries: payees registered by an account (first transfer to a new recipient, or an
  explicit `beneficiary_added` event such as an ATO adding a mule).
* Historical fraud cases with dispositions, including **cleared false positives**, so the
  system can learn that a shared device is not proof.
* The spec example scenario (spec §2): account A receives suspicious inflows and forwards
  them to three beneficiaries that share a device and IP; one beneficiary appeared in a
  confirmed prior case and another in an inconclusive one.
* Monitoring alerts, both true and false positives, with ground truth kept separately.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from .attacks import _fresh_identity, _recruit, _ring, _role, _span
from .world import World

FRAUD_ALERT_ROLES = ("mule_tier1", "collection_mule", "shell_company", "farmed_account", "synthetic_identity")


def beneficiary_id(owner_account: str, target_account: str) -> str:
    return "BN" + hashlib.sha1(f"{owner_account}|{target_account}".encode()).hexdigest()[:10]  # noqa: S324 - id, not security


# ----------------------------------------------------------------------------- spec example
def spec_example(w: World) -> dict[str, Any]:
    cfg = w.cfg
    ring = _ring(
        w,
        "spec_example",
        1,
        "Account A collects suspicious inflows and forwards them to three beneficiaries that share a "
        "device and IP; one beneficiary was in a confirmed mule case, another in an inconclusive one.",
    )
    t0 = cfg.start + timedelta(days=min(20, cfg.days - 6), hours=9)
    a_cust = _recruit(w, 1)[0]
    acct_a = w.primary_account(a_cust)
    _role(ring, acct_a, "collection_mule")

    shared_dev = w.new_device("android")
    shared_ip = w.new_ip("vpn")
    ring["devices"].add(shared_dev)
    ring["ips"].add(shared_ip)
    bens: list[str] = []
    for i in range(3):
        c, acct = _fresh_identity(w, t0 - timedelta(days=w.r.randint(10, 25)))
        own = w.new_device("android")
        w.cust_devices[c] = [own, shared_dev]
        w.cust_ips[c] = [shared_ip, w.new_ip("residential")]
        for k in range(3):
            w.login(c, shared_dev if k % 2 == 0 else own, shared_ip, t0 - timedelta(days=5 - k, hours=i))
        _role(ring, acct, f"beneficiary_{i + 1}")
        bens.append(acct)

    # 1) multiple suspicious inflows: ATO-style drains + structured cash deposits
    victims = _recruit(w, 3)
    total_in = 0.0
    for j, v in enumerate(victims):
        vacct = w.primary_account(v)
        ring["victim_accounts"].add(vacct)
        ring["victim_customers"].add(v)
        amt = w.r.uniform(38_000, 49_000)
        ts = t0 + timedelta(hours=j * 3)
        w.txn(
            ts,
            vacct,
            amt,
            dst_account=acct_a,
            channel="imps",
            device=shared_dev,
            ip=shared_ip,
            is_fraud=True,
            ring_id=ring["ring_id"],
            pattern="spec_inflow",
        )
        total_in += amt
        _span(ring, ts)
    for j in range(2):
        amt = cfg.reporting_threshold * w.r.uniform(0.9, 0.99)
        ts = t0 + timedelta(hours=10 + j)
        w.txn(
            ts,
            None,
            amt,
            dst_account=acct_a,
            channel="cash_deposit",
            is_fraud=True,
            ring_id=ring["ring_id"],
            pattern="structuring",
        )
        total_in += amt

    # 2) A registers three new beneficiaries and forwards most of the money within hours
    a_dev = w.cust_devices[a_cust][0]
    for i, b in enumerate(bens):
        ts = t0 + timedelta(hours=13, minutes=5 * i)
        w.acct_event(
            a_cust, "beneficiary_added", ts, a_dev, shared_ip, detail=b, is_fraud=True, ring_id=ring["ring_id"]
        )
    for i, (b, share) in enumerate(zip(bens, (0.33, 0.30, 0.32), strict=True)):
        ts = t0 + timedelta(hours=14, minutes=20 * i)
        w.txn(
            ts,
            acct_a,
            total_in * share,
            dst_account=b,
            channel="imps",
            device=a_dev,
            ip=shared_ip,
            is_fraud=True,
            ring_id=ring["ring_id"],
            pattern="spec_forward",
        )
        _span(ring, ts)
        # 3) beneficiaries cash out
        w.txn(
            ts + timedelta(hours=w.r.uniform(2, 8)),
            b,
            total_in * share * 0.95,
            channel="atm",
            is_fraud=True,
            ring_id=ring["ring_id"],
            pattern="cash_out",
        )
    ring["spec"] = {
        "account_a": acct_a,
        "customer_a": a_cust,
        "beneficiaries": bens,
        "shared_device": shared_dev,
        "shared_ip": shared_ip,
    }
    return ring


# ----------------------------------------------------------------------------- beneficiaries
def build_beneficiaries(w: World) -> list[dict[str, Any]]:
    """One payee registration per (owner account -> recipient account), first time it is used or added."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    person_accounts = {a for c in w.persons for a in w.accounts_of(c)} | {a for r in w.rings for a in r["roles"]}
    for t in sorted(w.txns, key=lambda x: x["ts"]):
        src, dst = t["src_account"], t["dst_account"]
        if not src or not dst or (t["channel"] in ("internal", "neft", "rtgs") and src not in person_accounts):
            continue
        key = (src, dst)
        if key not in seen:
            seen[key] = {
                "beneficiary_id": beneficiary_id(src, dst),
                "owner_account": src,
                "target_account": dst,
                "added_at": t["ts"],
                "device_id": t["device_id"],
                "ip": t["ip"],
                "source": "first_transfer",
            }
    for e in w.account_events:
        if e["kind"] != "beneficiary_added":
            continue
        owner = w.primary_account(e["customer_id"])
        key = (owner, e["detail"])
        seen[key] = {
            "beneficiary_id": beneficiary_id(*key),
            "owner_account": owner,
            "target_account": e["detail"],
            "added_at": e["ts"],
            "device_id": e["device_id"],
            "ip": e["ip"],
            "source": "beneficiary_added",
        }
    return list(seen.values())


# ----------------------------------------------------------------------------- historical cases
def build_historical_cases(w: World, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Closed investigations from *before* the simulation window."""
    cfg = w.cfg
    cases: list[dict[str, Any]] = []
    n = 1000

    def case(
        typology: str,
        status: str,
        accounts: list[tuple[str, str]],
        summary: str,
        facts: list[str],
        outcome: str,
        days_before: int,
    ) -> dict[str, Any]:
        nonlocal n
        n += 1
        opened = cfg.start - timedelta(days=days_before)
        c = {
            "case_id": f"FC-2025-{n}",
            "typology": typology,
            "status": status,
            "opened_at": opened.isoformat(),
            "closed_at": (opened + timedelta(days=w.r.randint(10, 40))).isoformat(),
            "summary": summary,
            "key_facts": facts,
            "outcome_reason": outcome,
            "accounts": [{"account_id": a, "role": role} for a, role in accounts],
        }
        cases.append(c)
        return c

    # spec example: one beneficiary in a confirmed mule case, another in an inconclusive one
    b1, b2, b3 = spec["beneficiaries"]
    case(
        "money_mule",
        "confirmed_fraud",
        [(b2, "mule"), ("A9" + "00101", "controller")],
        "Account received third-party transfers and forwarded them within hours to cash-out accounts.",
        [
            "Funds received from 4 unrelated senders within 2 days",
            "Over 90% forwarded within 6 hours",
            "Login from a VPN exit node shared with another mule account",
        ],
        "Customer admitted to a 'payment processing job'. Account closed and reported.",
        140,
    )
    case(
        "money_mule",
        "inconclusive",
        [(b3, "subject")],
        "Account flagged for rapid inflow and outflow; review could not establish intent.",
        ["Two large inflows forwarded the same day", "No device overlap with known mules at the time"],
        "Insufficient evidence. Monitoring recommended.",
        95,
    )

    # one prior confirmed case per mule ring, involving a tier-2 mule
    for r in w.rings:
        if r["pattern"] == "mule_network":
            tier2 = [a for a, role in r["roles"].items() if role == "mule_tier2"]
            if tier2:
                case(
                    "money_mule",
                    "confirmed_fraud",
                    [(tier2[0], "mule")],
                    "Mule account used to layer proceeds of account takeover fraud.",
                    ["Inflows from accounts reporting unauthorised access", "Outflows to ATM cash-out within a day"],
                    "Confirmed mule. Account restricted; customer exited.",
                    w.r.randint(60, 200),
                )

    # cleared false positives: the system must learn that these signals were benign
    households = sorted(w.traps["household_shared_device"])
    if len(households) >= 2:
        fam = [c for c in households if w.customers[c]["address"] == w.customers[households[0]]["address"]][:3]
        case(
            "shared_device",
            "cleared",
            [(w.primary_account(c), "subject") for c in fam],
            "Several accounts used the same device; investigation found they belong to one household.",
            ["Same registered address", "Device is a family tablet", "Transfers are small and bidirectional"],
            "Cleared: legitimate family use of a shared device.",
            80,
        )
    if w.shopkeepers:
        case(
            "beneficiary_fan_in",
            "cleared",
            [(w.shopkeepers[0], "subject")],
            "Account receives payments from hundreds of customers.",
            ["Registered small business (kirana store)", "Many small UPI payments, no rapid outflow"],
            "Cleared: merchant collection account.",
            120,
        )
    phones = sorted(w.traps["benign_new_phone"])
    if phones:
        case(
            "account_takeover",
            "cleared",
            [(w.primary_account(phones[0]), "subject")],
            "New device and password reset followed by a large purchase.",
            ["Residential IP matching the customer's history", "Customer confirmed a new phone purchase"],
            "Cleared: customer changed phone.",
            60,
        )

    # external historical cases (accounts long closed): pattern knowledge for RAG
    library = [
        (
            "account_takeover",
            "confirmed_fraud",
            "Credential stuffing led to takeover; a new beneficiary was added and the balance drained within minutes.",
            [
                "Several failed logins from a datacenter IP",
                "Password reset from a new device",
                "Transfer to a beneficiary added 3 minutes earlier",
            ],
        ),
        (
            "synthetic_identity",
            "confirmed_fraud",
            "Fabricated identities sharing phone numbers built credit history, then maxed out on electronics on the same day.",
            ["Phone numbers reused across identities", "Emulator device fingerprint", "Coordinated bust-out purchases"],
        ),
        (
            "card_testing",
            "confirmed_fraud",
            "A bot validated stolen cards with micro-authorisations through rotating proxies.",
            ["Dozens of distinct cards from one device", "High decline rate", "Amounts below 10 INR"],
        ),
        (
            "round_tripping",
            "confirmed_fraud",
            "Shell firms with a common director cycled funds to inflate turnover.",
            ["Directed cycle of transfers with under 1% value loss", "Shared registered address"],
        ),
        (
            "money_mule",
            "cleared",
            "A student received rent contributions from flatmates and paid the landlord.",
            ["Inflows from people at the same address", "Single outflow to a long-standing payee"],
        ),
    ]
    for i, (typ, status, summary, facts) in enumerate(library):
        case(
            typ,
            status,
            [(f"A9{i:05d}", "subject")],
            summary,
            facts,
            "Confirmed and reported." if status == "confirmed_fraud" else "Cleared after review.",
            200 + 20 * i,
        )
    return cases


# ----------------------------------------------------------------------------- alerts
def build_alerts(w: World, spec: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (alerts, truth). Truth is kept out of the alert payload."""
    alerts: list[dict[str, Any]] = []
    truth: list[dict[str, Any]] = []

    def add(
        alert_type: str,
        severity: str,
        account: str,
        summary: str,
        txn_ids: list[str],
        *,
        is_fraud: bool,
        ring_id: str | None,
        pattern: str,
    ) -> None:
        ref = f"MON-{len(alerts) + 1:05d}"
        alerts.append(
            {
                "external_ref": ref,
                "alert_type": alert_type,
                "severity": severity,
                "subject_account_id": account,
                "subject_customer_id": w.accounts[account]["customer_id"],
                "summary": summary,
                "payload": {"triggering_txn_ids": txn_ids[:20]},
            }
        )
        truth.append({"external_ref": ref, "is_fraud": is_fraud, "ring_id": ring_id, "pattern": pattern})

    def txns_into(acct: str, k: int = 5) -> list[str]:
        return [t["txn_id"] for t in w.txns if t["dst_account"] == acct][:k]

    def txns_from(acct: str, k: int = 5) -> list[str]:
        return [t["txn_id"] for t in w.txns if t["src_account"] == acct][:k]

    add(
        "rapid_pass_through",
        "high",
        spec["account_a"],
        "Multiple large inflows followed by transfers to three newly added beneficiaries within hours.",
        txns_into(spec["account_a"]) + txns_from(spec["account_a"]),
        is_fraud=True,
        ring_id="RING-SPEC_EXAMPLE-1",
        pattern="spec_example",
    )

    for r in w.rings:
        roles = r["roles"]
        pick = next((a for a, role in roles.items() if role in FRAUD_ALERT_ROLES), None)
        if r["pattern"] == "mule_network" and pick:
            add(
                "rapid_pass_through",
                "high",
                pick,
                "Funds forwarded within hours of receipt on multiple occasions.",
                txns_into(pick),
                is_fraud=True,
                ring_id=r["ring_id"],
                pattern=r["pattern"],
            )
            victim = sorted(r["victim_accounts"])[0]
            add(
                "account_takeover",
                "critical",
                victim,
                "New device login followed by transfer to a new beneficiary.",
                txns_from(victim),
                is_fraud=True,
                ring_id=r["ring_id"],
                pattern="ato",
            )
        elif r["pattern"] in ("synthetic_identity", "promo_abuse") and pick:
            add(
                "shared_device_cluster",
                "medium",
                pick,
                "Device shared by several recently opened accounts.",
                txns_from(pick),
                is_fraud=True,
                ring_id=r["ring_id"],
                pattern=r["pattern"],
            )
        elif r["pattern"] == "round_tripping" and pick:
            add(
                "rapid_pass_through",
                "medium",
                pick,
                "Large transfers in and out of a business account.",
                txns_into(pick),
                is_fraud=True,
                ring_id=r["ring_id"],
                pattern=r["pattern"],
            )
        elif r["pattern"] == "card_testing":
            victim = sorted(r["victim_accounts"])[0]
            add(
                "card_testing",
                "high",
                victim,
                "Card used for micro-authorisations followed by large purchases.",
                txns_from(victim),
                is_fraud=True,
                ring_id=r["ring_id"],
                pattern=r["pattern"],
            )

    # false positives a naive monitoring system raises
    households = sorted(w.traps["household_shared_device"] - w.used)
    for c in households[:3]:
        acct = w.primary_account(c)
        add(
            "shared_device_cluster",
            "medium",
            acct,
            "Device shared with other customers.",
            txns_from(acct),
            is_fraud=False,
            ring_id=None,
            pattern="household_shared_device",
        )
    for acct in w.shopkeepers[1:3]:
        add(
            "beneficiary_fan_in",
            "medium",
            acct,
            "Account receives payments from many unrelated senders.",
            txns_into(acct),
            is_fraud=False,
            ring_id=None,
            pattern="shopkeeper_fan_in",
        )
    for c in sorted(w.traps["benign_new_phone"] - w.used)[:2]:
        acct = w.primary_account(c)
        add(
            "account_takeover",
            "high",
            acct,
            "Password reset from a new device followed by a large purchase.",
            txns_from(acct),
            is_fraud=False,
            ring_id=None,
            pattern="benign_new_phone",
        )
    for c in sorted(w.traps["household_shared_address_ip"] - w.used - w.traps["household_shared_device"])[:2]:
        acct = w.primary_account(c)
        add(
            "rapid_pass_through",
            "low",
            acct,
            "Salary received and partly transferred out the same week.",
            txns_into(acct),
            is_fraud=False,
            ring_id=None,
            pattern="ordinary_customer",
        )
    return alerts, truth

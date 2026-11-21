"""Black-box smoke test of a running deployment (D-054). Standard library only, so CI can run it against
`docker compose` without installing the project.

    python scripts/smoke_test.py --base-url http://localhost:8000 --password "$SEED_USER_PASSWORD"

It walks the critical path a real analyst takes and exits 1 at the first broken invariant:

  1. readiness passes (Postgres, Neo4j, checkpointer)
  2. analyst logs in and starts an investigation on an open alert (with an idempotency key; a retry returns the same id)
  3. the worker drives it to awaiting_review within the timeout
  4. every finding is supported by evidence refs that exist in the evidence list
  5. the analyst cannot approve their own investigation (four-eyes)
  6. a reviewer approves; the investigation completes and a report is rendered
  7. the audit hash chain verifies
  8. /metrics exposes the investigation counters

The seed profile must have run first (users and alerts).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


class SmokeFailure(Exception):
    pass


class Client:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        hdrs = {"Accept": "application/json", **(headers or {})}
        data: bytes | None = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - URL comes from the operator
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status not in expect:
            raise SmokeFailure(f"{method} {path} -> {status} (expected {expect}): {raw[:300]!r}")
        ctype_json = raw[:1] in (b"{", b"[")
        return status, (json.loads(raw) if ctype_json else raw.decode())

    def login(self, email: str, password: str) -> Client:
        other = Client(self.base, self.timeout)
        _, tok = other.request("POST", "/api/v1/auth/token", form={"username": email, "password": password})
        other.token = tok["access_token"]
        return other


def step(name: str) -> None:
    print(f"[smoke] {name}", flush=True)


def wait_ready(c: Client, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            status, body = c.request("GET", "/api/v1/health/ready", expect=(200, 503))
            if status == 200:
                return
            last = json.dumps(body)[:300]
        except (urllib.error.URLError, OSError, SmokeFailure) as exc:
            last = str(exc)
        time.sleep(2)
    raise SmokeFailure(f"not ready after {timeout_s:.0f}s: {last}")


def run(base_url: str, password: str, ready_timeout: float, investigation_timeout: float) -> None:
    anon = Client(base_url)
    step("waiting for readiness")
    wait_ready(anon, ready_timeout)

    analyst = anon.login("analyst@bank.test", password)
    reviewer = anon.login("reviewer@bank.test", password)
    auditor = anon.login("auditor@bank.test", password)

    _, page = analyst.request("GET", "/api/v1/alerts?status=open&limit=50")
    if not page["items"]:
        raise SmokeFailure("no open alerts; run the seed profile first")
    alert = page["items"][0]
    step(f"starting investigation on alert {alert['external_ref']}")

    key = f"smoke-{uuid.uuid4()}"
    body = {"alert_id": alert["id"]}
    _, accepted = analyst.request(
        "POST", "/api/v1/investigations", body, headers={"Idempotency-Key": key}, expect=(200, 201, 202)
    )
    inv_id = accepted["investigation_id"]
    _, again = analyst.request(
        "POST", "/api/v1/investigations", body, headers={"Idempotency-Key": key}, expect=(200, 201, 202)
    )
    if again["investigation_id"] != inv_id:
        raise SmokeFailure("idempotent retry created a second investigation")

    step("waiting for the worker to reach awaiting_review")
    deadline = time.monotonic() + investigation_timeout
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, detail = analyst.request("GET", f"/api/v1/investigations/{inv_id}")
        if detail["status"] in ("awaiting_review", "failed", "cancelled"):
            break
        time.sleep(1)
    if detail.get("status") != "awaiting_review":
        raise SmokeFailure(f"investigation status {detail.get('status')!r} at node {detail.get('current_node')!r}")
    if detail.get("risk_score") is None:
        raise SmokeFailure("no deterministic risk score at review time")
    step(f"awaiting review: risk {detail['risk_score']} ({detail['risk_category']})")

    _, evidence = analyst.request("GET", f"/api/v1/investigations/{inv_id}/evidence?limit=500")
    refs = {e["ref"] for e in evidence["items"]}
    _, findings = analyst.request("GET", f"/api/v1/investigations/{inv_id}/findings")
    if not evidence["items"] or not findings:
        raise SmokeFailure("investigation produced no evidence or no findings")
    for f in findings:
        if f["is_supported"] and not set(f["evidence_refs"]) <= refs:
            raise SmokeFailure(f"finding cites unknown evidence: {sorted(set(f['evidence_refs']) - refs)}")
    step(f"{len(findings)} findings, {len(refs)} evidence items, all citations resolve")

    analyst.request("POST", f"/api/v1/investigations/{inv_id}/review", {"decision": "approved"}, expect=(403,))
    step("four-eyes enforced: analyst cannot approve own work")

    reviewer.request(
        "POST",
        f"/api/v1/investigations/{inv_id}/review",
        {"decision": "approved", "comment": "smoke test approval"},
        expect=(200, 201, 202),
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, detail = reviewer.request("GET", f"/api/v1/investigations/{inv_id}")
        if detail["status"] == "completed":
            break
        time.sleep(1)
    if detail["status"] != "completed":
        raise SmokeFailure(f"approved investigation is {detail['status']!r}, not completed")
    _, report = reviewer.request("GET", f"/api/v1/investigations/{inv_id}/report")
    if not report["markdown"].strip() or not report["ruleset_version"]:
        raise SmokeFailure("report missing markdown or ruleset version")
    step(f"completed; report v{report['version']} (rules {report['ruleset_version']})")

    _, chain = auditor.request("GET", "/api/v1/audit/verify")
    if not chain["intact"]:
        raise SmokeFailure(f"audit chain broken at row {chain['first_broken_id']}")
    step(f"audit chain intact ({chain['rows_checked']} rows)")

    _, metrics = anon.request("GET", "/metrics")
    for name in ("fri_investigations_started_total", "fri_http_requests_total"):
        if name not in metrics:
            raise SmokeFailure(f"/metrics does not expose {name}")
    step("metrics exposed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--password", required=True, help="SEED_USER_PASSWORD used by the seed profile")
    ap.add_argument("--ready-timeout", type=float, default=120)
    ap.add_argument("--investigation-timeout", type=float, default=180)
    args = ap.parse_args()
    try:
        run(args.base_url, args.password, args.ready_timeout, args.investigation_timeout)
    except SmokeFailure as exc:
        print(f"[smoke] FAILED: {exc}", file=sys.stderr)
        return 1
    print("[smoke] PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

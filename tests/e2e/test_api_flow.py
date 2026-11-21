"""The whole request lifecycle over HTTP: login → alert → 202 → worker → poll → review → report."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.container import Container
from app.core.enums import Role
from app.main import create_app

pytestmark = pytest.mark.integration


async def _login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/token", data={"username": email, "password": "Passw0rd!x"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_full_http_lifecycle(
    e2e_settings: Any, db: Any, worker_factory: Any, alerts: list[dict[str, Any]], make_actor: Any
) -> None:
    from sqlalchemy import select

    from app.domains.identity.models import User

    analyst, reviewer, auditor = (
        await make_actor(Role.ANALYST),
        await make_actor(Role.REVIEWER),
        await make_actor(Role.AUDITOR),
    )
    async with db.read_session() as s:
        emails = {u.id: u.email for u in (await s.scalars(select(User))).all()}
    worker = await worker_factory()
    container = Container.build(e2e_settings)
    app = create_app(container=container)
    app.state.container = container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/api/v1/alerts")).status_code == 401
        a_h = await _login(client, emails[analyst.user_id])
        r_h = await _login(client, emails[reviewer.user_id])
        au_h = await _login(client, emails[auditor.user_id])
        bad = await client.post("/api/v1/auth/token", data={"username": emails[analyst.user_id], "password": "nope"})
        assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthenticated"

        spec = next(a for a in alerts if a["truth"]["pattern"] == "spec_example")
        body = {k: v for k, v in spec.items() if k != "truth"}
        r = await client.post("/api/v1/alerts", json=body, headers=a_h)
        assert r.status_code == 201
        assert (await client.post("/api/v1/alerts", json=body, headers=a_h)).status_code == 200  # idempotent
        assert (await client.post("/api/v1/alerts", json=body, headers=r_h)).status_code == 403  # reviewer can't
        alert_id = r.json()["id"]

        r = await client.post(
            "/api/v1/investigations", json={"alert_id": alert_id}, headers={**a_h, "Idempotency-Key": "k-123"}
        )
        assert r.status_code == 202 and r.headers["location"].endswith(r.json()["investigation_id"])
        again = await client.post(
            "/api/v1/investigations", json={"alert_id": alert_id}, headers={**a_h, "Idempotency-Key": "k-123"}
        )
        assert again.status_code == 202 and again.headers.get("idempotent-replay") == "true"
        inv = r.json()["investigation_id"]

        while await worker.run_once():
            pass
        detail = (await client.get(f"/api/v1/investigations/{inv}", headers=a_h)).json()
        assert detail["status"] == "awaiting_review" and detail["risk"]["category"] in ("high", "critical")

        ev = (await client.get(f"/api/v1/investigations/{inv}/evidence?level=confirmed_outcome", headers=a_h)).json()
        assert ev["total"] >= 1 and all(e["assertion_level"] == "confirmed_outcome" for e in ev["items"])
        graph = (await client.get(f"/api/v1/investigations/{inv}/graph", headers=a_h)).json()
        assert {"Account", "Device", "FraudCase"} <= {n["label"] for n in graph["nodes"]}
        assert all(e["properties"]["evidence_ref"] for e in graph["edges"])

        assert (
            await client.post(f"/api/v1/investigations/{inv}/review", json={"decision": "approved"}, headers=a_h)
        ).status_code == 403  # analyst role cannot review
        r = await client.post(
            f"/api/v1/investigations/{inv}/review",
            json={"decision": "approved", "comment": "Escalate to FIU"},
            headers=r_h,
        )
        assert r.status_code == 202
        while await worker.run_once():
            pass
        rep = await client.get(f"/api/v1/investigations/{inv}/report", headers=au_h)
        assert rep.status_code == 200 and rep.json()["content"]["human_review"]["decision"] == "approved"
        md = await client.get(f"/api/v1/investigations/{inv}/report?format=markdown", headers=au_h)
        assert md.headers["content-type"].startswith("text/markdown") and "# Investigation report" in md.text
        tl = (await client.get(f"/api/v1/investigations/{inv}/timeline", headers=a_h)).json()
        assert tl[0]["kind"] == "status" or tl[0]["kind"] == "node"
        chain = (await client.get("/api/v1/audit/verify", headers=au_h)).json()
        assert chain["intact"] and chain["rows_checked"] >= 4
        assert (await client.get("/api/v1/audit/verify", headers=a_h)).status_code == 403
        bad_body = await client.post("/api/v1/alerts", json={**body, "external_ref": "x y"}, headers=a_h)
        assert bad_body.status_code == 422 and "x y" not in bad_body.text  # input not echoed back
    await container.aclose()

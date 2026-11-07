import anyio
from fastapi.testclient import TestClient

from superagent.api.app import create_app


def test_console_and_api(make_env):
    env = make_env()  # heuristic fakes: fast tier escalates, expert resolves
    app = create_app(orchestrator=env.orch, run_worker=False)
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        tid = c.post("/api/dev/tickets", json={"title": "Printer jam on floor 3", "description": "paper stuck"}).json()["ticket_id"]

    anyio.run(env.drain)

    with TestClient(app) as c:
        board = c.get("/")
        assert board.status_code == 200 and tid in board.text
        case = c.get(f"/api/cases/{tid}").json()
        assert case["status"] == "proposed" and case["tiers_used"] == ["fast", "expert"]
        page = c.get(f"/cases/{tid}")
        assert page.status_code == 200 and "Approval needed" in page.text
        help_id = case["help_requests"][0]["id"]
        r = c.post(f"/cases/{tid}/help/{help_id}", data={"answer": "ok", "decision": "approve"}, follow_redirects=False)
        assert r.status_code == 303
        assert c.get(f"/api/cases/{tid}").json()["status"] == "resolved"
        m = c.get("/api/metrics").json()
        assert m["resolved_by_agent"] == 1 and m["resolved_by_tier"]["expert"] == 1
        assert c.post("/webhooks/servicenow", json={}).status_code == 400

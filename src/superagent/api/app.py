"""HTTP API + operator console.

* ``/``                  case board with KPIs
* ``/cases/{id}``        case detail: triage, case file, sandbox runs, timeline, help/approval forms
* ``/api/...``           JSON API (cases, events, metrics, manual intake, poll)
* ``/webhooks/...``      ServiceNow / Jira webhooks (shared secret header ``X-SuperAgent-Secret``)

Auth: in Azure, Container Apps built-in authentication (Entra ID) injects
``X-MS-CLIENT-PRINCIPAL-NAME``. Locally, set ``SA_CONSOLE_API_KEY`` or run with SA_ENVIRONMENT=local.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import get_settings
from ..container import build_orchestrator
from ..itil import PRIORITY_LABEL, sla_status
from ..models import TERMINAL_STATUSES, CaseStatus
from ..observability import setup_observability
from ..store.base import ConflictError

log = logging.getLogger("superagent.api")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["PRIORITY_LABEL"] = PRIORITY_LABEL


def create_app(orchestrator=None, run_worker: bool | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_observability(settings, "superagent-api")
        app.state.orch = orchestrator or build_orchestrator()
        app.state.worker_task = None
        inproc = settings.run_worker_inproc if run_worker is None else run_worker
        if inproc:
            from ..worker import Worker

            w = Worker(app.state.orch, settings.worker_concurrency, settings.poll_enabled)
            app.state.worker = w
            app.state.worker_task = asyncio.create_task(w.run())
            log.info("running worker in-process (local mode)")
        yield
        if app.state.worker_task:
            app.state.worker.stop()
            app.state.worker_task.cancel()

    app = FastAPI(title="SuperAgent", version="1.0.0", lifespan=lifespan)

    def orch(request: Request):
        return request.app.state.orch

    def operator(request: Request) -> str:
        name = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
        if name:
            return name
        key = settings.console_api_key
        if key:
            supplied = request.headers.get("X-API-Key") or request.query_params.get("key") or request.cookies.get("sa_key", "")
            if hmac.compare_digest(supplied, key):
                return "api-key-operator"
            raise HTTPException(401, "authentication required")
        if settings.environment == "local":
            return "local-operator"
        raise HTTPException(401, "authentication required (configure Entra ID auth or SA_CONSOLE_API_KEY)")

    def webhook_auth(request: Request) -> None:
        secret = settings.webhook_secret
        if not secret:
            if settings.environment == "local":
                return
            raise HTTPException(403, "webhook secret not configured")
        if not hmac.compare_digest(request.headers.get("X-SuperAgent-Secret", ""), secret):
            raise HTTPException(403, "invalid webhook secret")

    # ------------------------------------------------------------------ health
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(o=Depends(orch)):
        await o.s.store.list(limit=1)
        return {"status": "ready"}

    # ------------------------------------------------------------------ JSON API
    async def _metrics(o) -> dict:
        cases = await o.s.store.list(limit=1000)
        done = [c for c in cases if c.status in TERMINAL_STATUSES]
        resolved = [c for c in done if c.status == CaseStatus.RESOLVED]
        by_tier = {"fast": 0, "expert": 0}
        for c in resolved:
            if c.last_outcome:
                by_tier[c.last_outcome.tier] = by_tier.get(c.last_outcome.tier, 0) + 1
        return {
            "cases_total": len(cases),
            "open": len(cases) - len(done),
            "resolved_by_agent": len(resolved),
            "handed_over": len(done) - len(resolved),
            "auto_resolution_rate": round(len(resolved) / len(done), 3) if done else None,
            "resolved_by_tier": by_tier,
            "waiting_human": sum(1 for c in cases if c.status in (CaseStatus.WAITING_HUMAN, CaseStatus.PROPOSED)),
            "sla_breached_open": sum(1 for c in cases if sla_status(c, o.policy) == "breached"),
            "ai_cost_usd_total": round(sum(c.total_cost for c in cases), 4),
            "ai_cost_usd_per_case": round(sum(c.total_cost for c in cases) / len(cases), 4) if cases else 0,
            "expert_spend_today_usd": round(o.s.router.expert_spend_today(), 4),
        }

    @app.get("/api/metrics")
    async def metrics(o=Depends(orch), user=Depends(operator)):
        return await _metrics(o)

    @app.get("/api/cases")
    async def list_cases(o=Depends(orch), user=Depends(operator), status: str | None = None):
        st = [CaseStatus(x) for x in status.split(",")] if status else None
        return [c.model_dump(mode="json", exclude={"ticket": {"raw"}}) for c in await o.s.store.list(status=st)]

    @app.get("/api/cases/{case_id}")
    async def get_case(case_id: str, o=Depends(orch), user=Depends(operator)):
        c = await o.s.store.get(case_id)
        if not c:
            raise HTTPException(404)
        return c.model_dump(mode="json")

    @app.get("/api/cases/{case_id}/events")
    async def case_events(case_id: str, o=Depends(orch), user=Depends(operator)):
        return [e.model_dump(mode="json") for e in await o.s.store.events(case_id)]

    @app.post("/api/tickets/{ticket_id}/process")
    async def process_ticket(ticket_id: str, o=Depends(orch), user=Depends(operator)):
        if await o.intake(ticket_id, reason=f"manual:{user}"):
            return {"ticket_id": ticket_id, "queued": True, "note": None}
        if await o.requeue(ticket_id, reason=f"manual:{user}"):
            return {"ticket_id": ticket_id, "queued": True, "note": "existing open case queued again"}
        return {"ticket_id": ticket_id, "queued": False, "note": "case exists and is closed or waiting for a person"}

    @app.post("/api/cases/{case_id}/help/{help_id}")
    async def api_answer(case_id: str, help_id: str, body: dict, o=Depends(orch), user=Depends(operator)):
        try:
            c = await o.apply_human_response(case_id, help_id, body.get("answer", ""), user, body.get("approved"))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ConflictError as e:
            raise HTTPException(409, "case was updated concurrently, retry") from e
        return {"status": c.status.value}

    @app.post("/api/poll")
    async def poll_now(request: Request, o=Depends(orch), user=Depends(operator)):
        from ..worker import Worker

        return {"new_tickets": await Worker(o).poll_once()}

    @app.post("/api/dev/tickets")
    async def dev_create_ticket(body: dict, o=Depends(orch), user=Depends(operator)):
        """Create a ticket in the MOCK ITSM (for demos and smoke tests)."""
        from ..itsm.mock import MockITSM

        if not isinstance(o.s.itsm, MockITSM):
            raise HTTPException(400, "only available with the mock ITSM")
        import uuid

        tid = body.get("id") or f"INC{uuid.uuid4().int % 10**7:07d}"
        o.s.itsm.add(tid, body["title"], body.get("description", ""),
                     **{k: v for k, v in body.items() if k in ("category", "impact", "urgency", "requester", "type")})  # fmt: skip
        await o.intake(tid, reason=f"dev:{user}")
        return {"ticket_id": tid}

    # ------------------------------------------------------------------ webhooks
    @app.post("/webhooks/servicenow", dependencies=[Depends(webhook_auth)])
    async def sn_webhook(body: dict, o=Depends(orch)):
        number = body.get("number") or (body.get("record") or {}).get("number")
        if not number:
            raise HTTPException(400, "missing 'number'")
        return {"queued": await o.intake(number, reason="webhook:servicenow")}

    @app.post("/webhooks/jira", dependencies=[Depends(webhook_auth)])
    async def jira_webhook(body: dict, o=Depends(orch)):
        key = (body.get("issue") or {}).get("key") or body.get("key")
        if not key:
            raise HTTPException(400, "missing issue key")
        return {"queued": await o.intake(key, reason="webhook:jira")}

    # ------------------------------------------------------------------ console (HTML)
    @app.get("/", response_class=HTMLResponse)
    async def board(request: Request, o=Depends(orch), user=Depends(operator)):
        cases = await o.s.store.list(limit=300)
        resp = templates.TemplateResponse(request, "board.html", {
            "cases": cases, "m": await _metrics(o), "user": user, "policy": o.policy,
            "sla": {c.id: sla_status(c, o.policy) for c in cases},
            "is_mock": o.s.itsm.name == "mock"})  # fmt: skip
        if request.query_params.get("key"):
            resp.set_cookie("sa_key", request.query_params["key"], httponly=True, samesite="strict", secure=True)
        return resp

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    async def case_page(case_id: str, request: Request, o=Depends(orch), user=Depends(operator)):
        c = await o.s.store.get(case_id)
        if not c:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "case.html", {
            "c": c, "events": await o.s.store.events(case_id), "user": user,
            "sla": sla_status(c, o.policy)})  # fmt: skip

    @app.post("/cases/{case_id}/help/{help_id}")
    async def answer_form(case_id: str, help_id: str, answer: str = Form(""), decision: str = Form("answer"),
                          o=Depends(orch), user=Depends(operator)):  # fmt: skip
        approved = {"approve": True, "reject": False}.get(decision)
        try:
            await o.apply_human_response(case_id, help_id, answer, user, approved)
        except ConflictError:
            return RedirectResponse(f"/cases/{case_id}?conflict=1", status_code=303)
        return RedirectResponse(f"/cases/{case_id}", status_code=303)

    @app.post("/ui/new-ticket")
    async def ui_new_ticket(title: str = Form(...), description: str = Form(""), o=Depends(orch), user=Depends(operator)):
        await dev_create_ticket({"title": title, "description": description}, o, user)
        return RedirectResponse("/", status_code=303)

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError):
        return JSONResponse({"detail": "conflict"}, status_code=409)

    return app


app = create_app()

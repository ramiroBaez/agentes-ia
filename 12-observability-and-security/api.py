"""The API: what a client, a human or Prometheus asks of the agent.

Three audiences, three doors:

  - **Integrators**: `POST /chat` and `POST /chat/stream` with an API key.
  - **Operators**: traces, approvals and feedback with backoffice scopes.
  - **Infrastructure**: `/health`, `/health/ready` and `/metrics`, with no
    credential (Prometheus has no keys) and without leaking anything about clients.

Four decisions worth keeping in mind while reading:

1. **The API never sees an API key in the clear after it is created.** The hash
   lives in SQLite; here it is only compared. A 401 does not say *why* it
   failed, so nobody can enumerate valid keys by probing.
2. **A refund is not approved twice.** The panel decides on the approval
   record, not on an in-memory flag: if the turn was already resolved, the
   second response is a 409 and the tool does not run again.
3. **Every security decision is audited**, not only the successful ones: a
   failed attempt is evidence too.
4. **The stream shows what is happening, not an animation.** Spans come off
   the event bus while the agent runs, and at the end the stream is completed
   with what ended up in the database (in case the client connected late).

Usage:
    uvicorn api:app --reload
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

import metrics
import notes
import security
from agent import Agent
from guardrails import Guardrails
from persistence import Database
from security import (
    AuthenticationError,
    Identity,
    JWTIssuer,
    KeyManager,
    Limiter,
    SecurityError,
    SessionCookies,
)
from settings import Settings, settings
from traces import REQUEST_ID, TYPE_SPAN_END, EventBus, Tracer

log = logging.getLogger("aurora.api")

VERSION = "12.0.0"

#: Scopes an endpoint can require (the list lives in `security`).
SCOPE_CHAT = security.SCOPE_CHAT
SCOPE_TRACES = security.SCOPE_TRACES
SCOPE_APPROVALS = security.SCOPE_APPROVALS

HEADERS_SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


# ---------------------------------------------------------------------- contracts
class TurnInput(BaseModel):
    """What an integrator or the browser chat sends."""

    model_config = {"extra": "forbid"}

    message: str = Field(min_length=1, max_length=8_000)
    conversation_id: str | None = None


class DecisionInput(BaseModel):
    """What a person answers in the approvals panel."""

    model_config = {"extra": "forbid"}

    decision: str = Field(pattern="^(approved|rejected)$")
    note: str = Field(default="", max_length=1_000)
    decider: str = Field(default="", max_length=80)


class FeedbackInput(BaseModel):
    """Thumbs up/down on an agent response."""

    model_config = {"extra": "forbid"}

    message_id: str = Field(min_length=1, max_length=64)
    value: int = Field(description="1 (good) or -1 (bad)")
    comment: str = Field(default="", max_length=2_000)


# ------------------------------------------------------------------ construction
def create_app(
    *,
    db: Database | None = None,
    config: Settings | None = None,
    model: Any = None,
    record: metrics.Record | None = None,
    bus: EventBus | None = None,
) -> FastAPI:
    """Build the app with its dependencies. Used by the tests and the real startup."""
    config = config or settings
    db = db or Database(path=config.DB_PATH)
    record = record or metrics.Record(config=config)
    bus = bus or EventBus()
    tracer = Tracer(db, bus=bus, record=record)
    agent = Agent(
        db=db,
        tracer=tracer,
        guardrails=Guardrails(config),
        model=model,
        config=config,
    )

    @asynccontextmanager
    async def cycle(app: FastAPI):
        # The API creates the schema itself on startup. If it depended on
        # someone running the CLI first, a `docker compose up` on a new machine
        # would leave the whole app without a database: /health/ready would fail
        # and the first chat would return 500. `init` is idempotent, so it is harmless.
        await anyio.to_thread.run_sync(db.init)
        # The notes index is built in a separate thread: if it ran during
        # startup, the API would not come up until all the embeddings finish.
        # Meanwhile the tool reports that it is still getting ready.
        notes.start_index_in_background()
        metrics.record_in_prometheus(record)
        log.info("Aurora Shop Ops %s listening (environment=%s)", VERSION, config.ENVIRONMENT)
        yield
        await app.state.limit.close()

    app = FastAPI(
        title="Aurora Shop Ops",
        version=VERSION,
        description="Agente de soporte con observabilidad, guardrails y aprobación humana.",
        lifespan=cycle,
    )
    app.state.config = config
    app.state.db = db
    app.state.record = record
    app.state.bus = bus
    app.state.tracer = tracer
    app.state.agent = agent
    app.state.manager = KeyManager(db)
    app.state.issuer = JWTIssuer(config)
    app.state.cookies = SessionCookies(config=config, issuer=app.state.issuer)
    app.state.limit = Limiter(config=config)

    if config.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.CORS_ORIGINS),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        )

    _record_paths(app)
    return app


def _record_paths(app: FastAPI) -> None:
    @app.middleware("http")
    async def headers_and_errors(request: Request, call_next):
        """Security headers on every response, no exceptions."""
        try:
            response: Response = await call_next(request)
        except SecurityError as e:
            response = _error(e)
        for name, value in HEADERS_SECURITY.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(SecurityError)
    async def handle_error(request: Request, exc: SecurityError):
        """Security errors go out with their code and a stable reason."""
        return _error(exc)

    # ------------------------------------------------------------- dependencies
    async def identity(request: Request) -> Identity:
        """API key if the header is present; otherwise, the session cookie.

        An integrator and a browser come in through the same door: the operator
        panel does not need a key of its own.
        """
        authorization = request.headers.get("Authorization")
        if authorization:
            key = authorization.removeprefix("Bearer ").strip()
            client = request.app.state.manager.authenticate(key, origin=_origin(request))
            if client is None:
                _audit(
                    request,
                    None,
                    "access_denied",
                    "api_key",
                    detail={"endpoint": request.url.path},
                    actor=_origin(request),
                )
                # Same message for "does not exist", "revoked" or "no header":
                # an attacker cannot enumerate keys by probing.
                raise AuthenticationError("Credencial inválida o ausente.")
            return Identity.from_client(client)
        return request.app.state.cookies.read(
            dict(request.cookies),
            write=request.method != "GET",
            headers={"X-CSRF-Token": request.headers.get("X-CSRF-Token", "")},
        )

    def with_scope(scope: str):
        async def dependency(ident: Identity = Depends(identity)) -> Identity:
            return security.require_scope(ident, scope)

        return dependency

    async def with_quota(request: Request, ident: Identity = Depends(identity)) -> dict:
        """Consume plan quota; if exceeded, 429 with `Retry-After`."""
        return await request.app.state.limit.record(
            f"{ident.client_id}:{ident.plan}", ident.plan
        )

    # ------------------------------------------------------------------- health
    @app.get("/health", tags=["infra"])
    def health() -> dict:
        """Liveness: the process responds. Needs neither credentials nor a database."""
        return {"status": "ok", "version": VERSION, "service": "aurora-shop-ops"}

    @app.get("/health/ready", tags=["infra"])
    def ready(request: Request) -> JSONResponse:
        """Readiness: besides responding, it can do work (that is, the database is reachable)."""
        try:
            request.app.state.db.connection().execute("SELECT 1 FROM traces LIMIT 1").fetchone()
        except (sqlite3.Error, OSError) as e:
            log.warning("readiness ko: %s", e)
            return JSONResponse(
                status_code=503,
                content={"status": "ko", "version": VERSION, "detail": "base no disponible"},
            )
        return JSONResponse(content={"status": "ok", "version": VERSION, "detail": "ready"})

    @app.get("/metrics", response_class=PlainTextResponse, tags=["infra"])
    def prometheus_metrics(request: Request) -> str:
        """The Prometheus text. No keys: Prometheus has no API key."""
        record: metrics.Record = request.app.state.record
        metrics.export(record)
        return record.render()

    # -------------------------------------------------------------------- chat
    @app.post("/chat", tags=["chat"])
    def chat(
        request: Request,
        response: Response,
        body: TurnInput,
        ident: Identity = Depends(with_scope(SCOPE_CHAT)),
        quota: dict = Depends(with_quota),
    ) -> dict:
        """One chat turn, end to end. The response carries its `trace_id`."""
        app_ = request.app
        timer = app_.state.record.start(client_id=ident.client_id, endpoint="/chat")
        try:
            result = app_.state.agent.turn(
                client_id=ident.client_id,
                message=body.message,
                endpoint="/chat",
                conversation_id=body.conversation_id,
            )
        except Exception as e:
            app_.state.record.error(timer, error=str(e))
            _audit(request, ident, "chat_error", "conversation", detail={"error": str(e)[:200]})
            log.exception("turn failed client=%s", ident.client_id)
            raise SecurityError("No pude procesar el mensaje.", code=500) from e

        app_.state.record.close(timer, status=result["status"])
        _audit(
            request,
            ident,
            "chat",
            "conversation",
            entity_id=result["conversation_id"],
        )
        for name, value in quota.headers.items():
            response.headers[name] = value
        return {
            "status": result["status"],
            "response": result["response"],
            "trace_id": result["trace_id"],
            "conversation_id": result["conversation_id"],
            "flags": result["flags"],
            "approval": result["approval"],
            # So the integrator can answer with a thumbs up on that response.
            "message_id": result.get("message_id"),
        }

    @app.post("/chat/stream", tags=["chat"])
    async def chat_stream(
        request: Request,
        body: TurnInput,
        ident: Identity = Depends(with_scope(SCOPE_CHAT)),
        quota: dict = Depends(with_quota),
    ) -> StreamingResponse:
        """The same turn, but spans are emitted over SSE while it runs."""
        app_ = request.app
        timer = app_.state.record.start(
            client_id=ident.client_id, endpoint="/chat/stream"
        )
        bus: EventBus = app_.state.bus
        status = {"result": None, "trace_id": None}
        seen: set[int] = set()
        # The trace is opened in the agent thread: we look it up by this request
        # and not by "the latest one", which with two simultaneous chats would be
        # the other one's.
        request_id = uuid.uuid4().hex

        async def run_agent() -> None:
            """The agent is synchronous: it runs in a thread and the stream follows it."""
            status["result"] = await anyio.to_thread.run_sync(
                lambda: app_.state.agent.turn(
                    client_id=ident.client_id,
                    message=body.message,
                    endpoint="/chat/stream",
                    conversation_id=body.conversation_id,
                )
            )

        async def events():
            token = REQUEST_ID.set(request_id)
            try:
                async with anyio.create_task_group() as group:
                    group.start_soon(run_agent)
                    yield _sse("start", {"client": ident.name, "endpoint": "/chat/stream"})
                    while status["result"] is None:
                        if status["trace_id"] is None and (
                            new := app_.state.tracer.last(request_id)
                        ):
                            status["trace_id"] = new
                            bus.subscribe(new)
                        event = (
                            bus.next(status["trace_id"], 0.02)
                            if status["trace_id"]
                            else None
                        )
                        if event and event.get("type") == TYPE_SPAN_END:
                            # One span per event: the closing one carries name, duration and status.
                            seen.add(event.get("seq", -1))
                            yield _sse("span", event)
                        else:
                            await anyio.sleep(0.01)

                    result = status["result"]
                    # Anything not seen live (the client connected late) is filled in.
                    for span in _spans_of(app_, result["trace_id"]):
                        if span["seq"] not in seen:
                            seen.add(span["seq"])
                            yield _sse("span", span)
                    app_.state.record.close(timer, status=result["status"])
                    _audit(
                        request,
                        ident,
                        "chat",
                        "conversation",
                        entity_id=result["conversation_id"],
                    )
                    yield _sse("end", result)
            finally:
                REQUEST_ID.reset(token)
                app_.state.tracer.forget(request_id)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",  # nginx must not buffer the stream
                **quota.headers,
            },
        )

    # --------------------------------------------------------------- backoffice
    @app.get("/traces", tags=["backoffice"])
    def list_traces(
        request: Request,
        limit: int = 50,
        status: str | None = None,
        ident: Identity = Depends(with_scope(SCOPE_TRACES)),
    ) -> dict:
        """The latest traces. Each client sees its own; the admin sees all."""
        only_own = not ident.has_scope(security.SCOPE_ADMIN)
        traces = request.app.state.db.get_traces(
            client_id=ident.client_id if only_own else None,
            limit=min(limit, 200),
            status=status,
        )
        return {"traces": traces}

    @app.get("/traces/{trace_id}", tags=["backoffice"])
    def get_trace(
        trace_id: str,
        request: Request,
        ident: Identity = Depends(with_scope(SCOPE_TRACES)),
    ) -> dict:
        """A trace with its spans. Another client's trace does not exist for you."""
        trace = request.app.state.db.get_trace(trace_id)
        if trace is None or not _can_view(ident, trace):
            raise SecurityError("Traza no encontrada.", code=404, reason="not_found")
        return trace

    @app.get("/traces/{trace_id}/events", tags=["backoffice"])
    async def events_trace(
        trace_id: str,
        request: Request,
        ident: Identity = Depends(with_scope(SCOPE_TRACES)),
    ) -> StreamingResponse:
        """Live spans of a trace, for the observability panel."""
        trace = request.app.state.db.get_trace(trace_id)
        if trace is None or not _can_view(ident, trace):
            raise SecurityError("Traza no encontrada.", code=404, reason="not_found")
        bus: EventBus = request.app.state.bus
        bus.subscribe(trace_id)

        async def flow():
            yield _sse("connected", {"trace_id": trace_id})
            # Without this check, closing the tab left the generator (and a pool
            # thread) waiting for events forever.
            while not await request.is_disconnected():
                event = await anyio.to_thread.run_sync(lambda: bus.next(trace_id, 0.25))
                if event:
                    yield _sse("span", event)

        return StreamingResponse(flow(), media_type="text/event-stream")

    @app.get("/approvals", tags=["backoffice"])
    def list_approvals(
        request: Request,
        status: str | None = "pending",
        ident: Identity = Depends(with_scope(SCOPE_APPROVALS)),
    ) -> dict:
        """The queue of actions waiting for a person.

        `status=all` also returns the already resolved ones: the panel shows
        them so the decision and its reason are not lost on reload.
        """
        filter = None if status == "all" else status
        approvals = request.app.state.db.list_approvals(status=filter)
        if not ident.has_scope(security.SCOPE_ADMIN):
            approvals = [a for a in approvals if a["client_id"] == ident.client_id]
        return {"approvals": approvals}

    @app.get("/approvals/{approval_id}", tags=["chat"])
    def get_approval(
        approval_id: str,
        request: Request,
        ident: Identity = Depends(identity),
    ) -> dict:
        """Where an approval stands, so the chat that requested it finds out.

        Whoever originated it (their own chat) or an admin can ask; it does not
        require the approvals scope because it decides nothing. Once resolved,
        it carries `response`: what the agent told the customer after the
        decision (`None` while the turn is still closing).
        """
        db = request.app.state.db
        approval = db.get_approval(approval_id)
        if approval is None or not _can_view(ident, approval):
            raise SecurityError("Aprobación no encontrada.", code=404, reason="not_found")
        response = None
        if approval["status"] != "pending" and approval.get("conversation_id"):
            later = [
                m
                for m in db.list_messages(approval["conversation_id"])
                if m["role"] == "agent" and m["created"] >= (approval.get("decided") or "")
            ]
            response = later[0]["text"] if later else None
        return {"approval": approval, "response": response}

    @app.post("/approvals/{approval_id}/decision", tags=["backoffice"])
    def decide_approval(
        approval_id: str,
        request: Request,
        body: DecisionInput,
        ident: Identity = Depends(with_scope(SCOPE_APPROVALS)),
    ) -> dict:
        """The person decides. If approved, the agent resumes and runs the action."""
        app_ = request.app
        timer = app_.state.record.start(
            client_id=ident.client_id, endpoint="/approvals/decision"
        )
        approval = app_.state.db.get_approval(approval_id)
        if approval is None or not _can_view(ident, approval):
            raise SecurityError("Aprobación no encontrada.", code=404, reason="not_found")
        if approval["status"] != "pending":
            # 409: the panel must not let a repeated click charge twice.
            app_.state.record.close(timer, status="ok")
            raise SecurityError(
                "La aprobación ya estaba resuelta.", code=409, reason="already_resolved"
            )

        try:
            result = app_.state.agent.resume(
                client_id=approval["client_id"],
                conversation_id=approval["conversation_id"],
                trace_id=approval["trace_id"],
                response_human={
                    "approval_id": approval_id,
                    "decision": body.decision,
                    "note": body.note,
                    "decider": body.decider or ident.name or "operator",
                },
            )
        except Exception as e:
            # For example, a checkpoint lost on restart: the approval stays
            # pending and the operator sees a clear error.
            app_.state.record.error(timer, error=str(e))
            _audit(
                request,
                ident,
                "approval_error",
                "approval",
                entity_id=approval_id,
                client_id=approval["client_id"],
                detail={"error": str(e)[:200]},
            )
            log.exception("could not resume approval %s", approval_id)
            raise SecurityError("No pude aplicar la decisión.", code=500) from e
        app_.state.record.human_decision(approved=body.decision == "approved")
        app_.state.record.close(timer, status=result["status"])
        _audit(
            request,
            ident,
            f"approval_{body.decision}",
            "approval",
            entity_id=approval_id,
            client_id=approval["client_id"],
            detail={"note": body.note},
        )
        return {
            "status": result["status"],
            "response": result["response"],
            "trace_id": result["trace_id"],
            "approval": result["approval"],
        }

    @app.post("/feedback", status_code=201, tags=["backoffice"])
    def feedback(
        request: Request,
        body: FeedbackInput,
        ident: Identity = Depends(with_scope(SCOPE_CHAT)),
    ) -> dict:
        """Thumbs up/down on an agent response.

        Sent by whoever received the response (same scope as chat), not by an
        operator: feedback has to come from the person who read it.
        """
        try:
            return request.app.state.db.record_feedback(
                body.message_id,
                body.value,
                client_id=ident.client_id,
                comment=body.comment,
            )
        except ValueError as e:
            raise SecurityError(str(e), code=422, reason="invalid_feedback") from e

    @app.get("/clients", tags=["backoffice"])
    def list_clients(
        request: Request, ident: Identity = Depends(with_scope(security.SCOPE_ADMIN))
    ) -> dict:
        """The platform clients. Admin only."""
        return {"clients": request.app.state.db.list_clients()}

    @app.post("/maintenance/purge", tags=["backoffice"])
    def purge(
        request: Request,
        days: int | None = None,
        ident: Identity = Depends(with_scope(security.SCOPE_ADMIN)),
    ) -> dict:
        """Data retention, run by hand. Returns how many rows it removed."""
        counts = request.app.state.db.purge_old(days)
        _audit(request, ident, "purge", "database", detail=counts)
        return {"purged": counts}

    # -------------------------------------------------------------------- auth
    @app.get("/session", tags=["auth"])
    def session_current(ident: Identity = Depends(identity)) -> dict:
        """Who is currently on the screen.

        The SPA asks on load: if the cookie is still alive it goes straight in,
        and if not, it asks for the API key again instead of showing a broken screen.
        """
        return {
            "client_id": ident.client_id,
            "name": ident.name,
            "scopes": list(ident.scopes),
            "plan": ident.plan,
        }

    @app.post("/session", tags=["auth"])
    def start_session(request: Request, response: Response) -> dict:
        """Exchange an API key for a session cookie (panel login)."""
        authorization = request.headers.get("Authorization", "")
        key = authorization.removeprefix("Bearer ").strip()
        client = request.app.state.manager.authenticate(key, origin=_origin(request))
        if client is None:
            raise AuthenticationError("Credencial inválida o ausente.")
        identity = Identity.from_client(client)
        for value in request.app.state.cookies.set(identity).values():
            response.headers.append("Set-Cookie", value)
        _audit(
            request,
            identity,
            "login",
            "client",
            entity_id=client["id"],
            client_id=client["id"],
        )
        return {"client": client, "csrf_header": "X-CSRF-Token"}

    @app.delete("/session", tags=["auth"])
    def close_session(request: Request, response: Response) -> dict:
        """Logout: the session cookies are cleared."""
        for value in request.app.state.cookies.clean().values():
            response.headers.append("Set-Cookie", value)
        return {"status": "ok"}


# ---------------------------------------------------------------------- helpers
def _error(exc: SecurityError) -> JSONResponse:
    """The error the client sees: stable, short and free of internal details."""
    response = JSONResponse(status_code=exc.code, content=exc.to_response())
    for name, value in (exc.headers or {}).items():
        response.headers[name] = value
    return response


def _sse(type: str, data: dict) -> str:
    """One SSE event.

    The type goes inside the JSON and not in the `event:` field so the client
    can dispatch everything from a single `onmessage` without registering
    twenty listeners.
    """
    return f"data: {json.dumps({'type': type, 'data': data}, ensure_ascii=False)}\n\n"


def _is_trusted_proxy(ip: str, config: Settings) -> bool:
    """Does the connection come from a proxy whose stated IP we believe?"""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False  # not even an IP (the TestClient host, a name)
    for rule in config.TRUSTED_PROXIES:
        rule = (rule or "").strip()
        if not rule:
            continue
        try:
            if "/" in rule:
                if address in ipaddress.ip_network(rule, strict=False):
                    return True
            elif str(address) == rule:
                return True
        except ValueError:  # garbage rule in the config: ignored, not fatal
            log.warning("TRUSTED_PROXIES has an invalid value: %r", rule)
    return False


def _origin(request: Request) -> str:
    """The caller's IP, for brute-force counting.

    Anyone can send `X-Forwarded-For`, so it is only believed when the
    connection comes from a trusted proxy: otherwise an attacker picks their
    own IP in the header and dodges the lockout at will.
    """
    direct = request.client.host if request.client else ""
    config = getattr(request.app.state, "config", settings)
    if not _is_trusted_proxy(direct, config):
        return direct or "unknown"
    # The chain is "client, proxy1, proxy2": take the first IP that is not
    # another trusted proxy, which is the one the client set.
    for candidate in (request.headers.get("x-forwarded-for") or "").split(","):
        candidate = candidate.strip()
        if candidate and not _is_trusted_proxy(candidate, config):
            return candidate
    return direct or "unknown"


def _can_view(ident: Identity, row: dict) -> bool:
    """The admin sees everything; everyone else, only their own."""
    if ident.has_scope(security.SCOPE_ADMIN):
        return True
    return row.get("client_id") == ident.client_id


def _spans_of(app_: FastAPI, trace_id: str) -> list[dict]:
    """The already persisted spans of a trace, for whoever arrives late to the SSE."""
    trace = app_.state.db.get_trace(trace_id) or {}
    return [
        {
            "trace_id": trace_id,
            "seq": span["seq"],
            "span_type": span["type"],
            "name": span["name"],
            "duration_ms": span["duration_ms"],
            "status": span["status"],
        }
        for span in trace.get("spans") or []
    ]


def _audit(
    request: Request,
    ident: Identity | None,
    action: str,
    entity: str,
    *,
    entity_id: str | None = None,
    client_id: str | None = None,
    detail: Any = None,
    actor: str | None = None,
) -> None:
    """Append-only audit: who asked for what and from where."""
    request.app.state.db.record_audit(
        actor=actor or (ident.client_id if ident else _origin(request)),
        action=action,
        entity=entity,
        entity_id=entity_id,
        client_id=client_id or (ident.client_id if ident else None),
        detail=detail,
    )


#: The app uvicorn serves (`uvicorn api:app`). Tests build their own with
#: `create_app`, so it is built lazily here: importing the module must not
#: open databases or leave files in the folder.
_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    if name == "app":
        global _app
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

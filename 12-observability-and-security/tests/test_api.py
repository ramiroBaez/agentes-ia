"""Seam 8: the API. What a real client (or a human) asks of the agent.

Specification:
  - without a credential there is no chat: 401, and *why* is never leaked;
  - an API key authenticates by header, a browser session by cookie + CSRF;
  - scopes rule: a chat client sees no traces and approves no refunds;
  - the rate limit answers 429 with `Retry-After` and quota headers;
  - `/chat` returns the response and `/chat/stream` pushes it over SSE with
    live spans;
  - the backoffice sees traces and approvals and resolves them by calling the agent;
  - `/health` and `/metrics` need no credential and leak no client data;
  - the security headers are present, and the API does not accept just any origin.
"""

import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import anyio
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import api
import metrics
import model
import security
from persistence import Database
from settings import PlanQuota, Settings
from traces import REQUEST_ID


def _config(**kwargs) -> Settings:
    base = {
        "JWT_SECRET": "x" * 32,
        "CHECKPOINTER_PERSISTENT": False,
        "PLANS": {
            "free": PlanQuota(requests_per_minute=2, requests_per_day=100, tokens_per_day=1000),
            "pro": PlanQuota(requests_per_minute=50, requests_per_day=1000, tokens_per_day=10_000),
        },
    }
    base.update(kwargs)
    return Settings(**base)


class FakeModel:
    """Answers with predefined turns. `repetitions` avoids having to count how
    many turns each test uses (and if they fall short, it still fails)."""

    def __init__(self, turns, repetitions=1):
        self.turns = list(turns) * repetitions
        self.seen = 0

    def __call__(self, messages, system="", funcs=None):
        self.seen += 1
        model.record_usage(
            {"tokens_input": 100, "tokens_output": 10, "tokens_total": 110},
            provider="gemini",
            model_name="gemini-2.5-flash",
        )
        return self.turns.pop(0)


class BlockedModel(FakeModel):
    """Like `FakeModel`, but does not answer until `wait` is set.

    Lets a test assert that something happened *while* the agent was thinking.
    `responded` makes clear when it finished thinking.
    """

    def __init__(self, turns, wait, responded=None):
        super().__init__(turns)
        self.wait = wait
        self.responded = responded or threading.Event()

    def __call__(self, messages, system="", funcs=None):
        self.wait.wait(5)
        self.responded.set()
        return super().__call__(messages, system, funcs)


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "api.db") as database:
        yield database


@pytest.fixture
def app(db):
    """An app with a chat client and a backoffice one."""
    config = _config()
    record = metrics.Record(config=config)
    app = api.create_app(
        db=db,
        config=config,
        model=FakeModel(
            [{"role": "agent", "text": "Hola, ¿en qué te ayudo?", "calls": []}],
            repetitions=20,
        ),
        record=record,
    )
    app.state.manager.generate_key(name="Chat Demo", scopes=["chat:write"], plan="free")
    return app


@pytest.fixture
def client_app(app):
    return TestClient(app)


@pytest.fixture
def keys(app):
    chat = app.state.manager.generate_key(name="Chat", scopes=["chat:write"], plan="free")
    admin = app.state.manager.generate_key(
        name="Ops", scopes=[security.SCOPE_ADMIN], plan="pro"
    )
    return {"chat": chat["key"], "admin": admin["key"]}


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


def _events(response):
    """The SSE events of a live response, already parsed."""
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def _lines_of_sse(piece: bytes) -> list[dict]:
    """The events in a chunk of an SSE response."""
    text = piece.decode()
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _consume_sse(app, path, headers, body, on_receive=None) -> list[bytes]:
    """Run the app the way uvicorn would and hand over each chunk on the fly.

    `TestClient` and `httpx.ASGITransport` keep the whole response until the
    end, so with them you cannot assert that an event went out *before* the
    agent finished. This helper shows the real order of the chunks.
    """
    pending = {"body": json.dumps(body).encode(), "read": False}
    received: list[bytes] = []
    headers = {"content-type": "application/json", **headers}

    async def receive():
        if not pending["read"]:
            pending["read"] = True
            return {"type": "http.request", "body": pending["body"], "more_body": False}
        # Like a real server: nothing arrives after the body. If `http.disconnect`
        # were returned here, Starlette would cut the stream (which is what it
        # does to hang up when the client leaves).
        await anyio.sleep_forever()

    async def main() -> None:
        async def send(message):
            if message["type"] != "http.response.body":
                return
            piece = message.get("body", b"")
            if not piece:
                return
            received.append(piece)
            if on_receive is not None:
                on_receive(piece)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                "client": ("127.0.0.1", 5555),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    anyio.run(main)
    return received


def _chat(client_app, key, body=None):
    """One chat turn with the given API key."""
    return client_app.post(
        "/chat", json=body or {"message": "hola"}, headers=_auth(key)
    ).json()


# ------------------------------------------------------------------ health and metrics
def test_health_answers_without_credentials(client_app):
    response = client_app.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"]


def test_the_api_creates_its_own_schema_on_startup(tmp_path):
    """A `docker compose up` on a new machine has to work right away.

    If the schema depended on someone running the CLI by hand, the app would
    start without a database: the readiness healthcheck returned 503 forever
    and the first chat a 500, without saying why.
    """
    empty = Database(path=tmp_path / "fresh.db")  # without `init()`
    with empty:
        app = api.create_app(
            db=empty,
            config=_config(),
            model=FakeModel([{"role": "agent", "text": "hola", "calls": []}]),
        )
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200
            assert client.get("/traces").status_code in (200, 401)


def test_readiness_checks_the_database(client_app, monkeypatch):
    def broken_db():
        raise sqlite3.OperationalError("no such table: traces")

    monkeypatch.setattr(client_app.app.state.db, "connection", broken_db)

    response = client_app.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "ko"


def test_a_spoofed_forwarded_for_cannot_escape_the_brute_force_lock(client_app, keys):
    """`X-Forwarded-For` cannot be used to choose the IP that gets counted.

    The header used to be read without checking who sent it: an attacker probed
    keys blindly sending a new IP on every attempt, so failures never piled up
    and the lockout never kicked in. Now the header is only believed if the
    connection comes from a trusted proxy, and TestClient is not one.
    """
    bad = "ak_" + "z" * 40

    for attempt in range(5):
        client_app.post(
            "/chat",
            json={"message": "hola"},
            headers={"Authorization": f"Bearer {bad}", "X-Forwarded-For": f"203.0.113.{attempt}"},
        )

    # The key is valid, but its (real) origin is already blocked: the header
    # IPs are not where anything was counted.
    response = client_app.post(
        "/chat",
        json={"message": "hola"},
        headers={**_auth(keys["chat"]), "X-Forwarded-For": "203.0.113.200"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["reason"] == "too_many_attempts"


def test_a_trusted_proxy_does_provide_the_real_ip(db, monkeypatch):
    """Behind nginx, the client IP arrives in the header and that one is used."""
    app = api.create_app(
        db=db,
        config=_config(TRUSTED_PROXIES=["127.0.0.1"]),
        model=FakeModel([{"role": "agent", "text": "hola", "calls": []}]),
    )

    def request(host: str, chain: str) -> str:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"x-forwarded-for", chain.encode())],
                "client": (host, 1234),
                "app": app,
            }
        )
        return api._origin(request)

    assert request("127.0.0.1", "198.51.100.7") == "198.51.100.7"
    # Multi-hop chain: the first IP that is not another proxy is taken.
    assert request("127.0.0.1", "198.51.100.7, 127.0.0.1") == "198.51.100.7"
    # An arbitrary client cannot declare its own IP.
    assert request("203.0.113.9", "198.51.100.7") == "203.0.113.9"


def test_metrics_is_prometheus_text_and_hides_client_ids(client_app, keys):
    client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))

    response = client_app.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "aurora_requests_total" in response.text
    assert keys["chat"] not in response.text


# -------------------------------------------------------------------- authentication
def test_chat_returns_the_answer_with_its_trace(client_app, keys):
    body = _chat(client_app, keys["chat"])

    assert body["response"] == "Hola, ¿en qué te ayudo?"
    assert body["trace_id"]
    assert body["conversation_id"]
    # The message id is what allows answering with a thumbs up on that response.
    assert body["message_id"]


def test_chat_without_a_key_is_401(client_app):
    response = client_app.post("/chat", json={"message": "hola"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == 401


def test_an_invalid_key_does_not_say_why(client_app):
    response = client_app.post("/chat", json={"message": "hola"}, headers=_auth("ak_inventada"))

    assert response.status_code == 401
    # Neither "does not exist" nor "revoked": the client cannot enumerate keys.
    body = response.text.lower()
    assert "exist" not in body and "revoc" not in body


def test_the_error_body_never_carries_internals(client_app):
    response = client_app.post("/chat", json={"message": "hola"})

    assert "traceback" not in response.text.lower()
    assert "ak_" not in response.text


# ------------------------------------------------------------------------ chat
def test_chat_answers_with_the_trace_id(client_app, keys):
    response = client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Hola, ¿en qué te ayudo?"
    assert body["trace_id"].startswith("tra_")
    assert body["status"] == "ok"


def test_chat_needs_the_right_scope(client_app, keys):
    """A backoffice key without `chat:write` can still chat: the admin can do everything."""
    client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["admin"]))

    only_read = client_app.app.state.manager.generate_key(
        name="Read only", scopes=["traces:read"]
    )

    response = client_app.post(
        "/chat", json={"message": "hola"}, headers=_auth(only_read["key"])
    )

    assert response.status_code == 403
    assert response.json()["error"]["reason"] == "without_permission"


def test_chat_validates_the_body(client_app, keys):
    response = client_app.post("/chat", json={"message": ""}, headers=_auth(keys["chat"]))
    empty = client_app.post("/chat", json={}, headers=_auth(keys["chat"]))

    assert response.status_code == 422
    assert empty.status_code == 422


def test_chat_sends_the_quota_headers(client_app, keys):
    response = client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))

    assert response.headers["X-RateLimit-Limit"] == "2"
    assert response.headers["X-RateLimit-Remaining"] == "1"


def test_the_rate_limit_answers_429_with_retry_after(client_app, keys):
    for _ in range(2):  # the fixture quota is 2 per minute
        client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))

    response = client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))

    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert response.json()["error"]["reason"] == "limit_exceeded"


def test_the_rate_limit_is_per_key(client_app, keys):
    for _ in range(3):
        client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))
    # The first key exhausted its plan...
    exhausted = client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))
    # ...but the other key still has its full quota.
    other = client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["admin"]))

    assert exhausted.status_code == 429
    assert other.status_code == 200


# ---------------------------------------------------------------------- streaming
def test_chat_stream_sends_sse_with_the_answer(client_app, keys):
    with client_app.stream(
        "POST", "/chat/stream", json={"message": "hola"}, headers=_auth(keys["chat"])
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response)

    types = [event["type"] for event in events]
    assert types[0] == "start"
    assert "end" in types
    end = events[types.index("end")]
    assert end["data"]["response"] == "Hola, ¿en qué te ayudo?"
    assert end["data"]["trace_id"]


def test_chat_stream_publishes_the_spans_while_it_runs(client_app, keys):
    with client_app.stream(
        "POST", "/chat/stream", json={"message": "hola"}, headers=_auth(keys["chat"])
    ) as response:
        events = _events(response)

    spans = [e for e in events if e["type"] == "span"]
    assert [s["data"]["name"] for s in spans] == [
        "guardrail_input",
        "agent",
        "guardrail_output",
    ]


def test_chat_stream_sends_a_span_before_the_model_answers(app, keys):
    """The first span has to travel while the agent is still working.

    If the stream only read the trace at the end, the panel would show
    everything at once and lose half the value of streaming.
    """
    wait = threading.Event()
    responded = threading.Event()
    app.state.agent.model = BlockedModel(
        [{"role": "agent", "text": "Hola, ¿en qué te ayudo?", "calls": []}],
        wait,
        responded,
    )
    in_live = []

    def on_receive(piece: bytes) -> None:
        for line in _lines_of_sse(piece):
            if line["type"] == "span" and not responded.is_set():
                in_live.append(line["data"]["name"])
                # The model was still waiting: this span went out live.
                wait.set()

    _consume_sse(
        app,
        "/chat/stream",
        _auth(keys["chat"]),
        {"message": "hola"},
        on_receive,
    )

    assert in_live == ["guardrail_input"]  # the `agent` node was still running
    assert responded.is_set()


def test_each_request_only_finds_its_own_trace(app):
    """Two simultaneous requests cannot end up seeing each other's trace.

    The trace is opened inside the agent (in another thread), so streaming
    looks it up by request id; otherwise two chats overlapping would be enough
    for one to see the other's spans.
    """
    tracer = app.state.tracer

    token = REQUEST_ID.set("request-a")
    try:
        own = tracer.open(client_id="cli_a", endpoint="/chat/stream")
    finally:
        REQUEST_ID.reset(token)
    foreign = tracer.open(client_id="cli_b", endpoint="/chat/stream")

    assert tracer.last("request-a") == own.id
    assert tracer.last("request-b") is None
    # Without a key the global lookup still works: the latest opened trace.
    assert tracer.last() == foreign.id


def test_importing_the_api_does_not_open_any_database(tmp_path):
    """`import api` has to be free: tests import the module and cannot leave an
    `agent.db` (or its checkpointer) in the project folder. The app is built
    when someone asks for it, as uvicorn does."""
    project = Path(__file__).resolve().parent.parent
    code = (
        "import pathlib, sys;"
        f"sys.path.insert(0, {str(project)!r});"
        "import api;"
        "print(sorted(p.name for p in pathlib.Path('.').glob('*.db')))"
    )
    output = subprocess.run(  # noqa: S603 - fixed command, no input data
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert output.stdout.strip() == "[]"


# -------------------------------------------------------------------- backoffice
def test_the_trace_list_needs_the_admin_scope(client_app, keys):
    without_permission = client_app.get("/traces", headers=_auth(keys["chat"]))
    allowed = client_app.get("/traces", headers=_auth(keys["admin"]))

    assert without_permission.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["traces"] == []


def test_a_trace_can_be_opened_with_its_spans(client_app, keys):
    created = _chat(client_app, keys["chat"])

    detail = client_app.get(
        f"/traces/{created['trace_id']}", headers=_auth(keys["admin"])
    )

    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == created["trace_id"]
    assert [s["name"] for s in body["spans"]][-1] == "guardrail_output"
    # The internal prompt does not travel in the readable trace.
    assert "system prompt" not in json.dumps(body).lower()


def test_a_trace_belongs_to_its_own_client(client_app, keys, app):
    created = _chat(client_app, keys["chat"])
    other = app.state.manager.generate_key(name="Other", scopes=["traces:read"], plan="pro")

    response = client_app.get(f"/traces/{created['trace_id']}", headers=_auth(other["key"]))

    # 404 and not 403: we do not even confirm the trace exists.
    assert response.status_code == 404


def test_the_approval_queue_is_visible_to_ops(client_app, keys, app, monkeypatch):
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
            {"role": "agent", "text": "Listo, te devolví $9.000.", "calls": []},
        ]
    )
    response = client_app.post(
        "/chat", json={"message": "el auricular llegó roto"}, headers=_auth(keys["chat"])
    ).json()
    assert response["status"] == "waiting_approval"

    queue = client_app.get("/approvals", headers=_auth(keys["admin"]))

    assert queue.status_code == 200
    assert [p["tool"] for p in queue.json()["approvals"]] == ["emit_refund"]
    assert queue.json()["approvals"][0]["args"]["amount"] == 9000.0


def test_the_chat_client_cannot_approve_anything(client_app, keys):
    response = client_app.post(
        "/approvals/apr_x/decision",
        json={"decision": "approved"},
        headers=_auth(keys["chat"]),
    )

    assert response.status_code == 403


def test_the_queue_hides_resolved_ones_unless_you_ask_for_them(client_app, keys, app):
    """`status=all` is what lets the panel keep the reason after a reload."""
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
        ]
    )
    _chat(client_app, keys["chat"])
    pending = client_app.get("/approvals", headers=_auth(keys["admin"])).json()[
        "approvals"
    ][0]
    client_app.post(
        f"/approvals/{pending['id']}/decision",
        json={"decision": "rejected", "note": "el cliente no lo pidió"},
        headers=_auth(keys["admin"]),
    )

    queue = client_app.get("/approvals", headers=_auth(keys["admin"]))
    assert queue.json()["approvals"] == []

    all = client_app.get("/approvals?status=all", headers=_auth(keys["admin"]))
    assert all.status_code == 200
    resolved = all.json()["approvals"][0]
    assert resolved["status"] == "rejected"
    assert resolved["reason"] == "el cliente no lo pidió"
    assert resolved["decided"], "without `decided` the panel cannot order the decisions"


def test_approving_from_the_panel_resumes_the_agent(client_app, keys, app):
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
            {"role": "agent", "text": "Listo, te devolví $9.000.", "calls": []},
        ]
    )
    paused = client_app.post(
        "/chat", json={"message": "el auricular llegó roto"}, headers=_auth(keys["chat"])
    ).json()
    approval = paused["approval"]

    response = client_app.post(
        f"/approvals/{approval['id']}/decision",
        json={"decision": "approved", "note": "ok", "decider": "ana"},
        headers=_auth(keys["admin"]),
    )

    assert response.status_code == 200
    # The response is what the model said plus customer and product: without the
    # raw tool phrase or the order status.
    text = response.json()["response"]
    assert text.startswith("Listo, te devolv")
    assert "Reembolso registrado" not in text
    assert "Estado del pedido" not in text


def test_rejecting_from_the_panel_does_not_run_the_tool(client_app, keys, app):
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
        ]
    )
    paused = client_app.post(
        "/chat", json={"message": "el auricular llegó roto"}, headers=_auth(keys["chat"])
    ).json()

    response = client_app.post(
        f"/approvals/{paused['approval']['id']}/decision",
        json={"decision": "rejected", "note": "el cliente tiene garantía"},
        headers=_auth(keys["admin"]),
    )

    assert response.status_code == 200
    assert "no aprobó" in response.json()["response"]


def test_deciding_twice_is_refused(client_app, keys, app):
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
            {"role": "agent", "text": "Listo.", "calls": []},
        ]
    )
    paused = client_app.post(
        "/chat", json={"message": "broken"}, headers=_auth(keys["chat"])
    ).json()
    path = f"/approvals/{paused['approval']['id']}/decision"
    body = {"decision": "approved", "note": "ok"}

    first = client_app.post(path, json=body, headers=_auth(keys["admin"]))
    second = client_app.post(path, json=body, headers=_auth(keys["admin"]))

    assert first.status_code == 200
    # A refund does not run twice even if the panel clicks twice.
    assert second.status_code == 409


def test_a_failed_resume_is_a_clean_error_and_leaves_the_approval_pending(
    client_app, keys, app
):
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
        ]
    )
    paused = client_app.post(
        "/chat", json={"message": "broken"}, headers=_auth(keys["chat"])
    ).json()

    def resume_broken(**_):
        raise RuntimeError("lost checkpoint")

    app.state.agent.resume = resume_broken
    response = client_app.post(
        f"/approvals/{paused['approval']['id']}/decision",
        json={"decision": "approved"},
        headers=_auth(keys["admin"]),
    )

    assert response.status_code == 500
    assert "checkpoint" not in response.text, "internal detail must not reach the client"
    pending = client_app.get("/approvals", headers=_auth(keys["admin"])).json()
    assert [a["id"] for a in pending["approvals"]] == [paused["approval"]["id"]]


def test_the_chat_that_asked_can_see_how_its_approval_ended(client_app, keys, app):
    app.state.agent.model = FakeModel(
        [
            {
                "role": "agent",
                "text": "",
                "calls": [
                    {"id": "c1", "name": "emit_refund",
                     "args": {"order": "A-1", "amount": 9000.0, "reason": "broken"}}
                ],
            },
        ]
    )
    paused = client_app.post(
        "/chat", json={"message": "broken"}, headers=_auth(keys["chat"])
    ).json()
    path = f"/approvals/{paused['approval']['id']}"

    before = client_app.get(path, headers=_auth(keys["chat"])).json()
    assert before["approval"]["status"] == "pending"
    assert before["response"] is None

    client_app.post(
        path + "/decision",
        json={"decision": "rejected", "note": "ya lo pidió por otro canal"},
        headers=_auth(keys["admin"]),
    )
    after = client_app.get(path, headers=_auth(keys["chat"])).json()

    assert after["approval"]["status"] == "rejected"
    assert after["approval"]["reason"] == "ya lo pidió por otro canal"
    assert "ya lo pidió por otro canal" in after["response"]


def test_feedback_is_recorded(client_app, keys):
    response = client_app.post(
        "/feedback",
        json={"message_id": "msg_x", "value": 1, "comment": "buenísimo"},
        headers=_auth(keys["chat"]),
    )

    assert response.status_code == 201
    assert response.json()["message_id"] == "msg_x"
    assert client_app.app.state.db.get_feedback("msg_x")["value"] == 1


def test_feedback_rejects_a_value_that_is_not_a_thumb(client_app, keys):
    response = client_app.post(
        "/feedback",
        json={"message_id": "msg_x", "value": 3},
        headers=_auth(keys["chat"]),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------------ session
def test_the_session_cookie_flow_needs_the_csrf_header(client_app, app):
    status = client_app.app.state
    register = status.manager.generate_key(name="Browser", scopes=["chat:write"], plan="free")
    identity = security.Identity.from_client(register["client"])
    values = _cookies_of(status.cookies.set(identity))

    without_csrf = client_app.post("/chat", json={"message": "hola"}, cookies=values)
    with_csrf = client_app.post(
        "/chat",
        json={"message": "hola"},
        cookies=values,
        headers={"X-CSRF-Token": values[status.config.CSRF_COOKIE_NAME]},
    )

    assert without_csrf.status_code == 403
    assert without_csrf.json()["error"]["reason"] == "invalid_csrf"
    assert with_csrf.status_code == 200


def test_the_session_cookie_is_http_only(client_app, app):
    status = client_app.app.state
    register = status.manager.generate_key(name="Browser", scopes=["chat:write"], plan="free")

    headers = status.cookies.set(security.Identity.from_client(register["client"]))

    session = headers[status.config.COOKIE_NAME]
    csrf = headers[status.config.CSRF_COOKIE_NAME]
    assert "HttpOnly" in session
    assert "SameSite=Strict" in session
    # The CSRF token is readable from JS: that is what makes it double-submit.
    assert "HttpOnly" not in csrf


def test_the_panel_can_ask_who_it_is(client_app, keys):
    """On reload, the SPA needs to know whether its cookie is still alive."""
    without_cookie = client_app.get("/session")
    with_key = client_app.get("/session", headers=_auth(keys["admin"]))

    assert without_cookie.status_code == 401
    assert with_key.status_code == 200
    body = with_key.json()
    assert body["name"] == "Ops"
    # The scopes are enough: the SPA does not reimplement the permission hierarchy.
    assert body["scopes"] == ["admin:all"]


def _cookies_of(headers: dict[str, str]) -> dict[str, str]:
    """Convert `{name: "Set-Cookie string"}` into a dict of client cookies."""
    return {name: value.partition("=")[2].split(";")[0] for name, value in headers.items()}


# ---------------------------------------------------------------------- headers
def test_the_security_headers_are_present(client_app):
    response = client_app.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-Frame-Options" in response.headers


def test_chat_answers_are_not_cached(client_app, keys):
    response = client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))

    assert response.headers["Cache-Control"] == "no-store"


def test_an_unknown_origin_is_not_allowed(client_app, keys):
    response = client_app.post(
        "/chat",
        json={"message": "hola"},
        headers={**_auth(keys["chat"]), "Origin": "https://sitio-pirata.com"},
    )

    assert response.headers.get("access-control-allow-origin") != "https://sitio-pirata.com"


def test_the_configured_origin_is_allowed(client_app, keys):
    response = client_app.post(
        "/chat",
        json={"message": "hola"},
        headers={**_auth(keys["chat"]), "Origin": "http://localhost:5173"},
    )

    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


# --------------------------------------------------------------------- audit
def test_every_security_decision_is_audited(client_app, keys, app):
    client_app.post("/chat", json={"message": "hola"}, headers=_auth(keys["chat"]))
    client_app.post("/chat", json={"message": "hola"}, headers=_auth("ak_mala"))

    actions = [a["action"] for a in app.state.db.list_audit()]

    # The accepted turn is recorded, and so is the attempt with an invalid key.
    assert "chat" in actions
    assert any("access" in action or "auth" in action for action in actions)

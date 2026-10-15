r"""Persistence for Project 12: SQLite (WAL) for clients, conversations,
messages, traces, approvals, audit and feedback.

Why SQLite and not Postgres: this project has to be cloned and run with a
single command, with no infrastructure. With `PRAGMA journal_mode=WAL` and one
connection per thread (FastAPI with 1 worker) it gives the concurrency it
needs, and the point to swap in Postgres is documented in the README (same
schema, other types).

Decisions that matter for security:
  - API keys are stored **hashed** (SHA-256): if someone reads the database
    file, they cannot authenticate;
  - all persisted text (chat messages, tool args, audit detail) goes through
    `redact`, which masks PII;
  - `purge_old` implements retention: what expired is deleted, and the
    conversations that go take their messages and traces with them.

Usage:
    from persistence import Database

    with Database("agent.db") as db:
        client = db.record_client("Aurora", key_hash, ["chat:write"], "free")
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# PII redaction lives in `pii` because the same rule must apply to what the
# model sees (guardrails) and to what is stored here.
from pii import redact
from settings import settings


def _redact_value(value: Any) -> Any:
    """Apply redaction to bare strings and to the values of dicts/lists."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _from_json(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


# --------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id            TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    scopes        TEXT NOT NULL DEFAULT '[]',
    plan          TEXT NOT NULL DEFAULT 'free',
    active        INTEGER NOT NULL DEFAULT 1,
    created        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL REFERENCES clients(id),
    title        TEXT,
    tokens        INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0.0,
    archived     INTEGER NOT NULL DEFAULT 0,
    created        TEXT NOT NULL,
    updated   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_client
    ON conversations(client_id, updated DESC);

CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL REFERENCES conversations(id),
    role               TEXT NOT NULL,
    text             TEXT NOT NULL,
    tool_calls        TEXT,
    provider         TEXT,
    model            TEXT,
    tokens            INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0.0,
    trace_id          TEXT,
    created            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created);

CREATE TABLE IF NOT EXISTS traces (
    id                TEXT PRIMARY KEY,
    client_id        TEXT,
    conversation_id   TEXT,
    endpoint          TEXT NOT NULL,
    provider         TEXT,
    model            TEXT,
    steps             INTEGER NOT NULL DEFAULT 0,
    tokens_input    INTEGER NOT NULL DEFAULT 0,
    tokens_output     INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0.0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'ok',
    flags             TEXT NOT NULL DEFAULT '[]',
    detail           TEXT,
    created            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_client ON traces(client_id, created DESC);

CREATE TABLE IF NOT EXISTS spans (
    trace_id    TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    type        TEXT NOT NULL,
    name      TEXT NOT NULL,
    args        TEXT,
    result   TEXT,
    detail     TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'ok',
    error       TEXT,
    PRIMARY KEY (trace_id, seq)
);

CREATE TABLE IF NOT EXISTS approvals (
    id                TEXT PRIMARY KEY,
    client_id        TEXT NOT NULL,
    conversation_id   TEXT,
    trace_id          TEXT,
    session            TEXT,
    tool              TEXT NOT NULL,
    args              TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'pending',
    requester       TEXT,
    decider         TEXT,
    reason            TEXT,
    created            TEXT NOT NULL,
    decided          TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created);

CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    TEXT,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    entity       TEXT NOT NULL,
    entity_id    TEXT,
    detail       TEXT,
    created        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_client ON audit(client_id, id DESC);

CREATE TABLE IF NOT EXISTS feedback (
    id            TEXT PRIMARY KEY,
    message_id    TEXT NOT NULL,
    client_id    TEXT,
    value         INTEGER NOT NULL,
    comment    TEXT,
    created        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);
"""

# Tables subject to retention, in the order they must be deleted (children first).
_TABLES_WITH_RETENTION = (
    "spans",
    "messages",
    "traces",
    "approvals",
    "conversations",
    "audit",
    "feedback",
)


class Database:
    """Access to the service database. Used as a context manager."""

    def __init__(
        self,
        path: str | Path,
        *,
        redact_pii: bool | None = None,
    ) -> None:
        self.path = Path(path)
        self.redact_pii = (
            settings.REDACT_PII if redact_pii is None else redact_pii
        )
        self._local = threading.local()
        self._lock_write = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def connection(self) -> sqlite3.Connection:
        """Connection of the current thread (SQLite does not share connections across threads)."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            self._local.connection = connection
        return connection

    def init(self) -> None:
        """Create the schema if it does not exist (idempotent)."""
        with self._lock_write:
            self.connection().executescript(SCHEMA)

    def close(self) -> None:
        """Close the current thread connection."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def __enter__(self) -> Database:
        self.init()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------- helpers
    def _text(self, text: str | None) -> str | None:
        """Apply PII redaction if enabled."""
        if text is None or not self.redact_pii:
            return text
        return redact(text)

    def _structure(self, value: Any) -> str | None:
        """Serialize to JSON, redacting any strings it contains."""
        if value is None:
            return None
        clean = _redact_value(value) if self.redact_pii else value
        return json.dumps(clean, ensure_ascii=False, default=str)

    def _row(self, row: sqlite3.Row | None, *, with_spans: bool = False) -> dict | None:
        """Convert an sqlite3.Row into a normalized dict (JSON -> objects)."""
        if row is None:
            return None
        data = dict(row)
        if "scopes" in data:
            data["scopes"] = _from_json(data["scopes"], [])
        if "flags" in data:
            data["flags"] = _from_json(data["flags"], [])
        if "tool_calls" in data:
            data["tool_calls"] = _from_json(data["tool_calls"], None)
        if "args" in data:
            data["args"] = _from_json(data["args"], {})
        if "detail" in data:
            data["detail"] = _from_json(data["detail"], None)
        for field in ("active", "archived"):
            if field in data:
                data[field] = bool(data[field])
        # The API key hash never leaves the data layer: it is not even logged.
        data.pop("key_hash", None)
        if with_spans:
            data["spans"] = self._spans(data["id"])
        return data

    def _rows(self, rows: Iterable[sqlite3.Row]) -> list[dict]:
        return [d for d in (self._row(f) for f in rows) if d is not None]

    def _spans(self, trace_id: str) -> list[dict]:
        rows = self.connection().execute(
            "SELECT seq, type, name, args, result, detail, duration_ms, status, error "
            "FROM spans WHERE trace_id = ? ORDER BY seq",
            (trace_id,),
        ).fetchall()
        spans = []
        for row in rows:
            span = dict(row)
            span["args"] = _from_json(span["args"], None)
            span["result"] = _from_json(span["result"], None)
            span["detail"] = _from_json(span["detail"], None)
            spans.append(span)
        return spans

    # ------------------------------------------------------------------- clients
    def record_client(
        self,
        name: str,
        key_hash: str,
        scopes: list[str],
        plan: str = "free",
    ) -> dict:
        """Register a client. `key_hash` is the SHA-256 of its API key."""
        client = {
            "id": _id("cli"),
            "name": name,
            "key_hash": key_hash,
            "scopes": scopes or [],
            "plan": plan or "free",
            "active": True,
            "created": _now(),
        }
        with self._lock_write:
            self.connection().execute(
                "INSERT INTO clients (id, name, key_hash, scopes, plan, active, created) "
                "VALUES (:id, :name, :key_hash, :scopes, :plan, :active, :created)",
                {**client, "scopes": json.dumps(client["scopes"]), "active": 1},
            )
        # The returned row never includes the hash: not even for convenience.
        client.pop("key_hash")
        return client

    def get_client_by_hash(self, key_hash: str) -> dict | None:
        """Active client matching that API key hash."""
        row = self.connection().execute(
            "SELECT * FROM clients WHERE key_hash = ? AND active = 1", (key_hash,)
        ).fetchone()
        return self._row(row)

    def get_client(self, client_id: str) -> dict | None:
        row = self.connection().execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        return self._row(row)

    def list_clients(self) -> list[dict]:
        rows = self.connection().execute("SELECT * FROM clients ORDER BY created").fetchall()
        return self._rows(rows)

    def deactivate_client(self, client_id: str) -> bool:
        """Revoke the client's access (its API key stops working)."""
        with self._lock_write:
            cursor = self.connection().execute(
                "UPDATE clients SET active = 0 WHERE id = ?", (client_id,)
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------- conversations
    def create_conversation(
        self,
        client_id: str,
        title: str | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        """Open a conversation. Its id is also the agent's `thread_id`."""
        now = _now()
        conversation = {
            "id": conversation_id or _id("con"),
            "client_id": client_id,
            "title": self._text(title),
            "tokens": 0,
            "cost_usd": 0.0,
            "archived": False,
            "created": now,
            "updated": now,
        }
        with self._lock_write:
            self.connection().execute(
                "INSERT INTO conversations "
                "(id, client_id, title, tokens, cost_usd, archived, created, updated) "
                "VALUES (:id, :client_id, :title, :tokens, :cost_usd, "
                ":archived, :created, :updated)",
                {**conversation, "archived": 0},
            )
        return conversation

    def get_conversation(self, conversation_id: str) -> dict | None:
        row = self.connection().execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return self._row(row)

    def list_conversations(
        self, client_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict]:
        rows = self.connection().execute(
            "SELECT * FROM conversations WHERE client_id = ? AND archived = 0 "
            "ORDER BY updated DESC LIMIT ? OFFSET ?",
            (client_id, limit, offset),
        ).fetchall()
        return self._rows(rows)

    def touch_conversation(
        self,
        conversation_id: str,
        tokens: int = 0,
        cost_usd: float = 0.0,
        title: str | None = None,
    ) -> None:
        """Update the conversation totals (tokens, cost, title)."""
        with self._lock_write:
            self.connection().execute(
                "UPDATE conversations SET tokens = tokens + ?, cost_usd = cost_usd + ?, "
                "updated = ?, title = COALESCE(?, title) WHERE id = ?",
                (tokens, cost_usd, _now(), self._text(title), conversation_id),
            )

    def archive_conversation(self, conversation_id: str) -> bool:
        with self._lock_write:
            cursor = self.connection().execute(
                "UPDATE conversations SET archived = 1, updated = ? WHERE id = ?",
                (_now(), conversation_id),
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ messages
    def add_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        *,
        tool_calls: Any = None,
        provider: str | None = None,
        model: str | None = None,
        tokens: int = 0,
        cost_usd: float = 0.0,
        trace_id: str | None = None,
    ) -> dict:
        """Store a thread message (read projection for the UI)."""
        message = {
            "id": _id("msg"),
            "conversation_id": conversation_id,
            "role": role,
            "text": self._text(text) or "",
            "tool_calls": self._structure(tool_calls),
            "provider": provider,
            "model": model,
            "tokens": tokens,
            "cost_usd": cost_usd,
            "trace_id": trace_id,
            "created": _now(),
        }
        with self._lock_write:
            self.connection().execute(
                "INSERT INTO messages "
                "(id, conversation_id, role, text, tool_calls, provider, model, tokens, "
                " cost_usd, trace_id, created) "
                "VALUES (:id, :conversation_id, :role, :text, :tool_calls, :provider, :model, "
                ":tokens, :cost_usd, :trace_id, :created)",
                message,
            )
        message["tool_calls"] = tool_calls
        return message

    def list_messages(self, conversation_id: str) -> list[dict]:
        rows = self.connection().execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created, rowid",
            (conversation_id,),
        ).fetchall()
        return self._rows(rows)

    # ------------------------------------------------------------------- traces
    def record_trace(self, trace: dict, spans: list[dict] | None = None) -> str:
        """Persist a trace with its spans and return its id."""
        trace_id = trace.get("id") or _id("tra")
        row = {
            "id": trace_id,
            "client_id": trace.get("client_id"),
            "conversation_id": trace.get("conversation_id"),
            "endpoint": trace.get("endpoint", "(desconocido)"),
            "provider": trace.get("provider"),
            "model": trace.get("model"),
            "steps": trace.get("steps", 0),
            "tokens_input": trace.get("tokens_input", 0),
            "tokens_output": trace.get("tokens_output", 0),
            "cost_usd": trace.get("cost_usd", 0.0),
            "duration_ms": trace.get("duration_ms", 0),
            "status": trace.get("status", "ok"),
            "flags": json.dumps(trace.get("flags") or [], ensure_ascii=False),
            "detail": self._structure(trace.get("detail")),
            "created": _now(),
        }
        with self._lock_write:
            connection = self.connection()
            connection.execute(
                "INSERT OR REPLACE INTO traces "
                "(id, client_id, conversation_id, endpoint, provider, model, steps, "
                " tokens_input, tokens_output, cost_usd, duration_ms, status, flags, "
                " detail, created) "
                "VALUES (:id, :client_id, :conversation_id, :endpoint, :provider, :model, "
                ":steps, :tokens_input, :tokens_output, :cost_usd, :duration_ms, :status, "
                ":flags, :detail, :created)",
                row,
            )
            for span in spans or []:
                connection.execute(
                    "INSERT OR REPLACE INTO spans "
                    "(trace_id, seq, type, name, args, result, detail, "
                    " duration_ms, status, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trace_id,
                        span.get("seq", 0),
                        span.get("type", "node"),
                        span.get("name", ""),
                        self._structure(span.get("args")),
                        self._structure(span.get("result")),
                        self._structure(span.get("detail")),
                        span.get("duration_ms", 0),
                        span.get("status", "ok"),
                        self._text(span.get("error")),
                    ),
                )
        return trace_id

    def get_trace(self, trace_id: str) -> dict | None:
        row = self.connection().execute(
            "SELECT * FROM traces WHERE id = ?", (trace_id,)
        ).fetchone()
        return self._row(row, with_spans=True)

    def get_traces(
        self,
        client_id: str | None = None,
        limit: int = 50,
        status: str | None = None,
    ) -> list[dict]:
        """List recent traces; without `client_id` anyone with admin scope sees them all."""
        conditions, params = [], []
        if client_id:
            conditions.append("client_id = ?")
            params.append(client_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection().execute(
            # `where` is built from literals in this function; values are bound (?).
            f"SELECT * FROM traces {where} ORDER BY created DESC LIMIT ?",  # noqa: S608
            (*params, limit),
        ).fetchall()
        return self._rows(rows)

    # ----------------------------------------------------------------- approvals
    def record_approval(
        self,
        *,
        client_id: str,
        tool: str,
        args: dict,
        requester: str | None = None,
        conversation_id: str | None = None,
        trace_id: str | None = None,
        session: str | None = None,
    ) -> dict:
        """Queue a sensitive action awaiting a human."""
        approval = {
            "id": _id("apr"),
            "client_id": client_id,
            "conversation_id": conversation_id,
            "trace_id": trace_id,
            "session": session,
            "tool": tool,
            "args": args or {},
            "status": "pending",
            "requester": requester,
            "decider": None,
            "reason": None,
            "created": _now(),
            "decided": None,
        }
        with self._lock_write:
            self.connection().execute(
                "INSERT INTO approvals "
                "(id, client_id, conversation_id, trace_id, session, tool, args, status, "
                " requester, decider, reason, created, decided) "
                "VALUES (:id, :client_id, :conversation_id, :trace_id, :session, :tool, :args, "
                ":status, :requester, :decider, :reason, :created, :decided)",
                {**approval, "args": self._structure(approval["args"]) or "{}"},
            )
        self.record_audit(
            client_id=client_id,
            actor=requester or "(agente)",
            action="approval_requested",
            entity="approval",
            entity_id=approval["id"],
            detail={"tool": tool, "args": approval["args"]},
        )
        return approval

    def get_approval(self, approval_id: str) -> dict | None:
        row = self.connection().execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        return self._row(row)

    def list_approvals(
        self,
        status: str | None = None,
        client_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if client_id:
            conditions.append("client_id = ?")
            params.append(client_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection().execute(
            # `where` is built from literals in this function; values are bound (?).
            f"SELECT * FROM approvals {where} ORDER BY created LIMIT ?",  # noqa: S608
            (*params, limit),
        ).fetchall()
        return self._rows(rows)

    def resolve_approval(
        self,
        approval_id: str,
        status: str,
        decider: str,
        reason: str | None = None,
    ) -> dict:
        """Close the approval ('approved' or 'rejected') and leave an audit entry."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"Estado de aprobación inválido: {status}")
        with self._lock_write:
            self.connection().execute(
                "UPDATE approvals SET status = ?, decider = ?, reason = ?, decided = ? "
                "WHERE id = ?",
                (status, decider, self._text(reason), _now(), approval_id),
            )
        current = self.get_approval(approval_id)
        self.record_audit(
            client_id=current["client_id"] if current else None,
            actor=decider,
            action="approval_resolved",
            entity="approval",
            entity_id=approval_id,
            detail={"status": status, "reason": reason},
        )
        return current

    # --------------------------------------------------------------------- audit
    def record_audit(
        self,
        *,
        actor: str,
        action: str,
        entity: str,
        client_id: str | None = None,
        entity_id: str | None = None,
        detail: Any = None,
    ) -> None:
        """Append-only: who did what. It is the system's audit evidence."""
        with self._lock_write:
            self.connection().execute(
                "INSERT INTO audit "
                "(client_id, actor, action, entity, entity_id, detail, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    client_id,
                    actor,
                    action,
                    entity,
                    entity_id,
                    self._structure(detail),
                    _now(),
                ),
            )

    def list_audit(
        self, client_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        if client_id:
            rows = self.connection().execute(
                "SELECT * FROM audit WHERE client_id = ? ORDER BY id DESC LIMIT ?",
                (client_id, limit),
            ).fetchall()
        else:
            rows = self.connection().execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return self._rows(rows)

    # ------------------------------------------------------------------- feedback
    def record_feedback(
        self,
        message_id: str,
        value: int,
        client_id: str | None = None,
        comment: str | None = None,
    ) -> dict:
        """Thumbs up/down on an agent reply."""
        if value not in (1, -1):
            raise ValueError("El feedback debe ser 1 (bien) o -1 (mal).")
        feedback = {
            "id": _id("fdb"),
            "message_id": message_id,
            "client_id": client_id,
            "value": value,
            "comment": self._text(comment),
            "created": _now(),
        }
        with self._lock_write:
            self.connection().execute(
                "INSERT INTO feedback (id, message_id, client_id, value, comment, created) "
                "VALUES (:id, :message_id, :client_id, :value, :comment, :created)",
                feedback,
            )
        return feedback

    def get_feedback(self, message_id: str) -> dict | None:
        """The feedback of a specific message (the dashboard uses it for the thumbs)."""
        row = self.connection().execute(
            "SELECT * FROM feedback WHERE message_id = ? ORDER BY created DESC LIMIT 1",
            (message_id,),
        ).fetchone()
        return self._row(row)

    def list_feedback(
        self, client_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        """The feedback received, to see what is failing."""
        if client_id:
            rows = self.connection().execute(
                "SELECT * FROM feedback WHERE client_id = ? ORDER BY created DESC LIMIT ?",
                (client_id, limit),
            ).fetchall()
        else:
            rows = self.connection().execute(
                "SELECT * FROM feedback ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
        return self._rows(rows)

    # ------------------------------------------------------------------ retention
    def purge_old(self, days: int | None = None) -> dict[str, int]:
        """Delete what expired under retention. Returns how many rows were removed per table.

        It stops at clients: an account is never removed for age, because it is
        master business data. And a `pending` approval is not touched either,
        however old: it is a person's work waiting, not history. If it were
        deleted, the paused turn would be left orphaned.
        """
        days = settings.RETENTION_DAYS if days is None else days
        if days <= 0:
            return dict.fromkeys(_TABLES_WITH_RETENTION, 0)
        cut = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        counts: dict[str, int] = {}
        with self._lock_write:
            connection = self.connection()
            for table in _TABLES_WITH_RETENTION:
                if table == "spans":
                    # Spans have no `created` of their own: they hang from their trace.
                    cursor = connection.execute(
                        "DELETE FROM spans WHERE trace_id IN "
                        "(SELECT id FROM traces WHERE created < ?)",
                        (cut,),
                    )
                elif table == "approvals":
                    cursor = connection.execute(
                        "DELETE FROM approvals WHERE created < ? AND status != 'pending'",
                        (cut,),
                    )
                else:
                    cursor = connection.execute(
                        f"DELETE FROM {table} WHERE created < ?", (cut,)  # noqa: S608 - fixed list
                    )
                counts[table] = cursor.rowcount
        return counts

"""Seam 3: persistence (clients, conversations, traces, approvals, audit),
PII redaction and retention purge.

Specification:
  - a client is registered with the **hash** of its API key and retrieved by
    that hash (the plain key is never stored or returned);
  - the messages of a conversation come back in order and a client's
    conversations are not mixed with another's;
  - a trace is persisted with its spans, in order, and can be read back
    filtered by client;
  - an approval starts `pending` and when resolved records who and why,
    leaving a trail in the audit log;
  - what is persisted goes through PII redaction (emails, cards) and the purge
    deletes only what expired according to retention.
"""

from datetime import UTC, datetime, timedelta

from persistence import Database, redact


def hash_key(key: str) -> str:
    """Hash of an API key: same algorithm security.py uses (SHA-256)."""
    import hashlib

    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _age(db: "Database", table: str, column: str, value: str, days: int) -> None:
    """Move rows into the past to test retention (tests only)."""
    moment = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with db.connection() as connection:
        connection.execute(
            f"UPDATE {table} SET created = ? WHERE {column} = ?",  # noqa: S608 - test fixtures
            (moment, value),
        )
        connection.commit()


def test_a_new_client_is_found_by_its_key_hash(db):
    client = db.record_client(
        "Aurora Labs", hash_key("llave-de-prueba"), ["chat:write"], "pro"
    )

    found = db.get_client_by_hash(hash_key("llave-de-prueba"))

    assert found["id"] == client["id"]
    assert found["name"] == "Aurora Labs"
    assert found["scopes"] == ["chat:write"]
    assert found["plan"] == "pro"
    assert found["active"] is True
    # The key is never returned, neither in plain text nor hashed.
    assert "llave-de-prueba" not in str(found)
    assert "key_hash" not in found


def test_an_unknown_key_hash_matches_no_client(db):
    db.record_client("Aurora Labs", hash_key("llave-de-prueba"), ["chat:write"])

    assert db.get_client_by_hash(hash_key("otra-llave")) is None


def test_messages_of_a_conversation_keep_their_order(db):
    client = db.record_client("Aurora", hash_key("k1"), ["chat:write"])
    conversation = db.create_conversation(client["id"], title="Primer contacto")

    db.add_message(conversation["id"], "user", "¿tenés auriculares?")
    db.add_message(conversation["id"], "agent", "Sí, hay 12 en stock")
    db.add_message(conversation["id"], "user", "¿cuánto sale?")

    messages = db.list_messages(conversation["id"])

    assert [m["role"] for m in messages] == ["user", "agent", "user"]
    assert messages[0]["text"] == "¿tenés auriculares?"
    assert messages[2]["conversation_id"] == conversation["id"]


def test_conversations_are_listed_per_client(db):
    labs = db.record_client("Labs", hash_key("k1"), ["chat:write"])
    store = db.record_client("Store", hash_key("k2"), ["chat:write"])
    conversation_of_labs = db.create_conversation(labs["id"], title="Soporte")
    db.create_conversation(store["id"], title="Otro cliente")

    of_labs = db.list_conversations(labs["id"])

    assert [c["id"] for c in of_labs] == [conversation_of_labs["id"]]


def test_pii_is_redacted_before_it_is_persisted(db):
    client = db.record_client("Aurora", hash_key("k1"), ["chat:write"])
    conversation = db.create_conversation(client["id"])
    text_with_pii = (
        "Escribile a ana.garcia@aurora.com y cobrale a la 4111 1111 1111 1111 "
        "por el pedido A-1042, son 12999.99"
    )

    db.add_message(conversation["id"], "user", text_with_pii)
    saved = db.list_messages(conversation["id"])[0]["text"]

    assert "ana.garcia@aurora.com" not in saved
    assert "4111 1111 1111 1111" not in saved
    # The rest of the useful information is kept: redaction does not delete the message.
    assert "A-1042" in saved
    assert "12999.99" in saved


def test_redaction_keeps_the_shape_of_the_message(db):
    masked = redact("mandame el resumen a ana@aurora.com, tarjeta 4111111111111111")

    assert "ana@aurora.com" not in masked
    assert "4111111111111111" not in masked
    assert "mandame el resumen a " in masked
    assert "tarjeta " in masked


def test_redaction_can_be_disabled_for_local_debug(tmp_path):
    base = Database(tmp_path / "sin-redaccion.db", redact_pii=False)
    with base:
        base.init()
        client = base.record_client("Debug", hash_key("k1"), ["chat:write"])
        conversation = base.create_conversation(client["id"])
        base.add_message(conversation["id"], "user", "escribime a ana@aurora.com")
        messages = base.list_messages(conversation["id"])

    assert messages[0]["text"] == "escribime a ana@aurora.com"


def test_a_trace_is_read_back_with_its_spans_in_order(db):
    labs = db.record_client("Labs", hash_key("k1"), ["chat:write"])
    store = db.record_client("Store", hash_key("k2"), ["chat:write"])

    created_id = db.record_trace(
        {
            "client_id": labs["id"],
            "conversation_id": None,
            "endpoint": "/chat/stream",
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "steps": 2,
            "tokens_input": 1200,
            "tokens_output": 300,
            "cost_usd": 0.0,
            "duration_ms": 4210,
            "status": "ok",
            "flags": ["possible_injection"],
        },
        spans=[
            {
                "seq": 0,
                "type": "node",
                "name": "agent",
                "duration_ms": 3000,
                "args": None,
                "result": None,
            },
            {
                "seq": 1,
                "type": "tool",
                "name": "search_product",
                "duration_ms": 12,
                "args": {"term": "auriculares"},
                "result": "1 resultado",
            },
            {"seq": 2, "type": "node", "name": "agent", "duration_ms": 1198},
        ],
    )

    trace = db.get_trace(created_id)
    assert trace["provider"] == "groq"
    assert trace["flags"] == ["possible_injection"]
    assert [s["name"] for s in trace["spans"]] == ["agent", "search_product", "agent"]
    assert [s["seq"] for s in trace["spans"]] == [0, 1, 2]

    # Isolation: the other client does not see someone else's trace.
    assert db.get_traces(store["id"]) == []
    assert len(db.get_traces(labs["id"])) == 1


def test_an_approval_starts_pending_and_can_be_resolved(db):
    labs = db.record_client("Labs", hash_key("k1"), ["chat:write", "refunds:approve"])

    pending = db.record_approval(
        client_id=labs["id"],
        conversation_id=None,
        tool="emit_refund",
        args={"amount": 25000.0, "reason": "Producto defectuoso"},
        requester="chat:write",
    )
    assert pending["status"] == "pending"
    assert pending["decider"] is None

    resolved = db.resolve_approval(
        pending["id"], "approved", "supervisor-ana", "OK, cliente verificado"
    )

    assert resolved["status"] == "approved"
    assert resolved["decider"] == "supervisor-ana"
    assert resolved["reason"] == "OK, cliente verificado"
    assert db.get_approval(pending["id"])["status"] == "approved"
    assert db.list_approvals(status="pending") == []
    assert [a["id"] for a in db.list_approvals(status="approved")] == [pending["id"]]


def test_resolving_an_approval_leaves_an_audit_trail(db):
    labs = db.record_client("Labs", hash_key("k1"), ["chat:write"])
    pending = db.record_approval(
        client_id=labs["id"],
        conversation_id=None,
        tool="emit_refund",
        args={"amount": 100.0},
        requester="chat:write",
    )

    db.resolve_approval(pending["id"], "rejected", "supervisor-luis", "Sin comprobante")
    events = db.list_audit(client_id=labs["id"])

    actions = [e["action"] for e in events]
    assert "approval_requested" in actions
    assert "approval_resolved" in actions
    resolution = next(e for e in events if e["action"] == "approval_resolved")
    assert resolution["actor"] == "supervisor-luis"
    assert resolution["detail"]["status"] == "rejected"


def test_purge_removes_only_expired_rows(db):
    labs = db.record_client("Labs", hash_key("k1"), ["chat:write"])
    old = db.create_conversation(labs["id"], title="Old")
    new = db.create_conversation(labs["id"], title="New")
    db.add_message(old["id"], "user", "mensaje viejo")
    db.add_message(new["id"], "user", "mensaje nuevo")
    db.record_trace(
        {
            "client_id": labs["id"],
            "conversation_id": old["id"],
            "endpoint": "/chat/stream",
            "provider": "groq",
            "model": "x",
            "steps": 1,
            "tokens_input": 1,
            "tokens_output": 1,
            "cost_usd": 0.0,
            "duration_ms": 10,
            "status": "ok",
            "flags": [],
        },
        spans=[],
    )
    # Age the old conversation (and what hangs from it) by 40 days.
    _age(db, "conversations", "id", old["id"], days=40)
    _age(db, "traces", "conversation_id", old["id"], days=40)
    _age(db, "messages", "conversation_id", old["id"], days=40)

    count = db.purge_old(days=30)

    assert count["conversations"] == 1
    assert count["messages"] == 1
    assert count["traces"] == 1
    assert db.get_conversation(old["id"]) is None
    assert db.get_conversation(new["id"]) is not None
    assert [m["text"] for m in db.list_messages(new["id"])] == ["mensaje nuevo"]

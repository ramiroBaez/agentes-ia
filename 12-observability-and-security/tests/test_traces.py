"""Seam 2: the tracer and the event bus.

Specification:
  - an agent turn (two model calls and a tool in between) leaves a persisted
    trace with its spans **in order**, with the tokens and cost accumulated
    from each model call;
  - guardrail flags accumulate on the trace;
  - the bus publishes, in the same order they happen, the spans and the trace
    close (this is what the UI streaming consumes);
  - a span that raises an exception is still recorded, marked as an error, and
    the exception keeps propagating upward.
"""

import hashlib

import pytest

import model
from traces import TYPE_SPAN_END, EventBus, Tracer


def test_a_simulated_agent_turn_is_persisted_with_its_spans_in_order(db):
    """A full turn leaves the trace ready for the UI to display."""
    tracer = Tracer(db)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")

    with tracer.span(trace, "node", "agent"):
        model.record_usage(
            {"tokens_input": 800, "tokens_output": 120, "tokens_total": 920},
            provider="gemini",
            model_name="gemini-2.5-flash",
        )
        tracer.record_usage(trace)
    with tracer.span(trace, "tool", "search_product", args={"term": "auriculares"}):
        pass
    with tracer.span(trace, "node", "agent"):
        model.record_usage(
            {"tokens_input": 1000, "tokens_output": 80, "tokens_total": 1080},
            provider="gemini",
            model_name="gemini-2.5-flash",
        )
        tracer.record_usage(trace)
    closed = tracer.close(trace, status="ok")

    saved = db.get_trace(closed["id"])
    assert [s["name"] for s in saved["spans"]] == ["agent", "search_product", "agent"]
    assert [s["type"] for s in saved["spans"]] == ["node", "tool", "node"]
    assert saved["spans"][1]["args"] == {"term": "auriculares"}
    # The tokens of the two model calls are summed on the trace.
    assert saved["tokens_input"] == 1800
    assert saved["tokens_output"] == 200
    assert saved["cost_usd"] > 0
    assert saved["status"] == "ok"
    assert saved["steps"] == 2
    assert saved["provider"] == "gemini"


def test_guardrail_flags_accumulate_on_the_trace(db):
    tracer = Tracer(db)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")

    tracer.mark(trace, "possible_injection")
    with tracer.span(
        trace, "guardrail", "injection_detection", detail={"pattern": "ignore previous"}
    ):
        pass
    tracer.mark(trace, "canary_exposed")
    closed = tracer.close(trace, status="ok")

    saved = db.get_trace(closed["id"])
    assert saved["flags"] == ["possible_injection", "canary_exposed"]
    assert any(s["type"] == "guardrail" for s in saved["spans"])


def test_the_bus_publishes_spans_and_trace_end_in_order(db):
    bus = EventBus()
    tracer = Tracer(db, bus=bus)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")
    bus.subscribe(trace.id)  # the consumer (SSE) subscribes when it connects
    events = list(bus.receive(trace.id))

    with tracer.span(trace, "node", "agent"):
        pass
    with tracer.span(
        trace, "tool", "calculate_total", args={"product": "monitor", "quantity": 2}
    ):
        pass
    tracer.close(trace, status="ok")

    while (event := bus.next(trace.id, timeout=0.2)) is not None:
        events.append(event)

    starts = [e for e in events if e["type"] == "span_start"]
    ends = [e for e in events if e["type"] == "span_end"]
    assert [e["name"] for e in starts] == ["agent", "calculate_total"]
    assert [e["name"] for e in ends] == ["agent", "calculate_total"]
    assert events[-1]["type"] == "trace_end"
    assert events[-1]["status"] == "ok"
    assert events[-1]["id"] == trace.id


def test_a_late_subscriber_gets_the_events_it_missed(db):
    """The SSE connects when it can: if the bus does not keep earlier events, the
    agent's first span is lost and the dashboard starts blind."""
    bus = EventBus()
    tracer = Tracer(db, bus=bus)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")

    with tracer.span(trace, "node", "agent"):  # happened before it connected
        pass

    bus.subscribe(trace.id)

    with tracer.span(trace, "node", "guardrail_output"):
        pass

    received = []
    while (event := bus.next(trace.id, timeout=0.2)) is not None:
        received.append(event)
    # One logical span = its closing event (the start one only marks that it began).
    names = [e["name"] for e in received if e["type"] == TYPE_SPAN_END]
    assert names == ["agent", "guardrail_output"]


def test_the_bus_forgets_the_traces_that_nobody_watches(db):
    bus = EventBus()
    tracer = Tracer(db, bus=bus)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])

    for _ in range(bus.TRACES_RETAINED + 10):
        trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")
        with tracer.span(trace, "node", "agent"):
            pass

    assert bus.traces_retained() <= bus.TRACES_RETAINED


def test_a_span_that_raises_is_still_recorded_as_an_error(db):
    tracer = Tracer(db)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")

    with pytest.raises(RuntimeError, match="the tool broke"), tracer.span(
        trace, "tool", "search_product", args={"term": "x"}
    ):
        raise RuntimeError("the tool broke")
    closed = tracer.close(trace, status="error", detail="the tool failed")

    saved = db.get_trace(closed["id"])
    span = saved["spans"][0]
    assert span["status"] == "error"
    assert "the tool broke" in span["error"]
    assert saved["status"] == "error"


def test_a_disconnected_subscriber_stops_receiving_events(db):
    bus = EventBus()
    tracer = Tracer(db, bus=bus)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")

    bus.subscribe(trace.id)
    assert bus.subscribers(trace.id) == 1
    bus.disconnect(trace.id)
    assert bus.subscribers(trace.id) == 0

    with tracer.span(trace, "node", "agent"):
        pass
    tracer.close(trace)

    # Nobody is listening: no events piled up and the tracer did not hang.
    assert bus.next(trace.id, timeout=0.05) is None


def test_the_trace_totals_match_what_the_persistence_layer_sums(db):
    """Two turns of the same client are stored as separate traces."""
    tracer = Tracer(db)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])

    for _ in range(2):
        trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")
        with tracer.span(trace, "node", "agent"):
            model.record_usage(
                {"tokens_input": 10, "tokens_output": 5, "tokens_total": 15},
                provider="gemini",
                model_name="gemini-2.5-flash",
            )
            tracer.record_usage(trace)
        tracer.close(trace)

    traces = db.get_traces(client["id"])
    assert len(traces) == 2
    assert all(t["tokens_input"] == 10 for t in traces)
    assert all(t["cost_usd"] > 0 for t in traces)


def test_a_backpressured_subscriber_never_blocks_the_agent(db):
    """If the SSE consumer does not read, the tracer keeps publishing anyway."""
    bus = EventBus(max_events=2)
    tracer = Tracer(db, bus=bus)
    client = db.record_client("Labs", _hash("k1"), ["chat:write"])
    trace = tracer.open(client_id=client["id"], endpoint="/chat/stream")
    bus.subscribe(trace.id)

    for i in range(20):
        with tracer.span(trace, "node", f"agent-{i}"):
            pass
    tracer.close(trace)

    # The queue stayed at its cap (dropping the old ones) instead of growing.
    assert len(list(bus.receive(trace.id))) <= 2
    assert db.get_trace(trace.id)["steps"] == 20


def test_persistence_layer_never_exposes_key_hashes(db):
    """Guardrail: the data layer does not expose API key hashes."""
    client = db.record_client("Labs", _hash("secret"), ["chat:write"])
    assert "key_hash" not in client
    assert "key_hash" not in db.get_client(client["id"])
    assert all("key_hash" not in c for c in db.list_clients())
    assert db.get_client_by_hash(_hash("secret"))["id"] == client["id"]


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

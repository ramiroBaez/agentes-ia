r"""Tracing for Project 12: what the agent did, in which order and at what cost.

Two pieces:

1. `EventBus`: an in-memory bus with one queue per trace. The API publishes
   spans as they happen and the streaming endpoint (SSE) reads them to show
   them live in the UI. It is what makes the chat feel "alive" instead of a
   text box that just waits.

2. `Tracer`: the trace of one request. It is opened at the start, spans are
   added to it (graph nodes, tools, guardrails, approval checkpoints) and when
   closed it is persisted with its tokens and cost.

Cost comes from the real usage reported by the provider (`model.last_usage()`),
not from a rough estimate: if the model has no price loaded, the cost stays at
0 and the trace is marked as not costable (`model.is_costable`).

Usage:
    tracer = Tracer(db, bus=EventBus())
    trace = tracer.open(client_id=..., endpoint="/chat/stream")
    with tracer.span(trace, "tool", "search_product", args={"term": "monitor"}):
        result = search_product("monitor")
    data = tracer.close(trace, status="ok")
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import model
from persistence import Database

if TYPE_CHECKING:  # `metrics` imports nothing from here: the dependency is one-way.
    from metrics import Record

#: Event types published on the bus.
TYPE_SPAN_START = "span_start"
TYPE_SPAN_END = "span_end"
TYPE_TRACE_END = "trace_end"

#: Id of the request in progress. The API sets it before launching the agent, so
#: the tracer can tag the trace as "this request's" and the streaming endpoint
#: can find it without mixing it up with another simultaneous chat. AnyIO copies
#: the context into the agent thread, so it works even if the trace is opened there.
REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)

#: How many unclosed requests the tracer remembers at most.
_MAX_REQUESTS = 512

#: Maximum queue size per subscriber. If the SSE client does not consume (slow
#: network, closed tab), the oldest events are dropped instead of letting memory
#: grow without bound.
MAX_EVENTS_PER_SUBSCRIBER = 500


class EventBus:
    """In-memory event bus: one FIFO queue per subscriber (per trace).

    It also keeps the latest events of each trace: the SSE consumer shows up
    when the request is already running, and without that history the first
    span is always lost. What is retained is bounded per trace and in number of
    traces, so a high-traffic process does not grow without limit.
    """

    #: How many traces keep events for late subscribers.
    TRACES_RETAINED = 100

    def __init__(
        self,
        max_events: int = MAX_EVENTS_PER_SUBSCRIBER,
        traces_retained: int | None = None,
    ) -> None:
        self._queues: dict[str, queue.Queue] = {}
        self._replay: OrderedDict[str, list[dict]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_events = max_events
        self._traces_retained = traces_retained or self.TRACES_RETAINED

    def subscribe(self, trace_id: str) -> None:
        """Register a consumer for the trace (idempotent: one per trace)."""
        with self._lock:
            pending = self._replay.get(trace_id, [])
            if trace_id in self._queues:
                inbox = self._queues[trace_id]
            else:
                inbox = queue.Queue(maxsize=self._max_events)
                self._queues[trace_id] = inbox
            for event in pending:
                _enqueue(inbox, event)

    def disconnect(self, trace_id: str) -> None:
        """Remove the consumer and free its queue (end of stream or client gone)."""
        with self._lock:
            self._queues.pop(trace_id, None)

    def subscribers(self, trace_id: str) -> int:
        with self._lock:
            return 1 if trace_id in self._queues else 0

    def traces_retained(self) -> int:
        """How many traces keep events for deferred replay."""
        with self._lock:
            return len(self._replay)

    def forget(self, trace_id: str) -> None:
        """Drop what is retained for a trace that nobody will watch anymore."""
        with self._lock:
            self._replay.pop(trace_id, None)

    def publish(self, trace_id: str, event: dict) -> None:
        """Publish an event. Never blocks nor propagates errors to the agent."""
        with self._lock:
            self._remember(trace_id, event)
            inbox = self._queues.get(trace_id)
        if inbox is None:
            return
        _enqueue(inbox, event)

    def _remember(self, trace_id: str, event: dict) -> None:
        """Keep the event for whoever subscribes later (call with the lock held)."""
        self._replay.setdefault(trace_id, []).append(event)
        del self._replay[trace_id][:-self._max_events]
        self._replay.move_to_end(trace_id)
        while len(self._replay) > self._traces_retained:
            self._replay.popitem(last=False)

    def next(self, trace_id: str, timeout: float | None = None) -> dict | None:
        """Next event of the trace, or None if the timeout expired."""
        with self._lock:
            inbox = self._queues.get(trace_id)
        if inbox is None:
            return None
        try:
            return inbox.get(timeout=timeout) if timeout else inbox.get_nowait()
        except queue.Empty:
            return None

    def receive(self, trace_id: str) -> Iterator[dict]:
        """Iterate over the already published events of the trace (snapshot, non-blocking)."""
        with self._lock:
            inbox = self._queues.get(trace_id)
        if inbox is None:
            return iter(())
        return iter(list(inbox.queue))


def _enqueue(inbox: queue.Queue, event: dict) -> None:
    """Put an event in the subscriber queue; if it is full, drop the oldest.

    Backpressure: we prefer losing the oldest event to growing without a cap.
    """
    try:
        inbox.put_nowait(event)
    except queue.Full:
        try:
            inbox.get_nowait()
            inbox.put_nowait(event)
        except (queue.Empty, queue.Full):  # pragma: no cover - extreme race
            pass


class Trace:
    """Trace open in memory. It is persisted when closed."""

    def __init__(
        self,
        *,
        client_id: str | None,
        conversation_id: str | None,
        endpoint: str,
    ) -> None:
        self.id = f"tra_{uuid.uuid4().hex[:12]}"
        self.client_id = client_id
        self.conversation_id = conversation_id
        self.endpoint = endpoint
        self.status = "in_progress"
        self.detail: Any = None
        self.flags: list[str] = []
        self.spans: list[dict] = []
        self.tokens_input = 0
        self.tokens_output = 0
        self.cost_usd = 0.0
        self.steps = 0
        self.costable = True
        self.provider: str | None = None
        self.model: str | None = None
        self.start = time.perf_counter()
        self.duration_ms = 0

    def mark(self, flag: str) -> None:
        """Add a guardrail flag to the trace (without duplicates)."""
        if flag not in self.flags:
            self.flags.append(flag)

    def to_dict(self) -> dict:
        provider = self.provider
        model_name = self.model
        if provider is None and model.LAST_PROVIDER != "(ninguno)":
            provider = model.LAST_PROVIDER
        if model_name is None:
            model_name = model.MODEL_ACTIVE
        return {
            "id": self.id,
            "client_id": self.client_id,
            "conversation_id": self.conversation_id,
            "endpoint": self.endpoint,
            "provider": provider,
            "model": model_name,
            "steps": self.steps,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_usd": round(self.cost_usd, 8),
            "duration_ms": self.duration_ms,
            "status": self.status,
            "flags": self.flags,
            "detail": self.detail,
        }


class Tracer:
    """Open, instrument and persist traces.

    If a `metrics.Record` is passed, every span, every token usage and every
    guardrail flag is counted there too: the tracer is the only place that sees
    everything go by, so it is the one that feeds `/metrics`.
    """

    def __init__(
        self,
        db: Database,
        bus: EventBus | None = None,
        record: Record | None = None,
    ) -> None:
        self.db = db
        self.bus = bus or EventBus()
        self.record = record
        self._lock = threading.Lock()
        self._last: str | None = None
        self._per_request: dict[str, str] = {}

    def last(self, request_id: str | None = None) -> str | None:
        """The id of the last trace opened.

        Used by the streaming endpoint: the trace is opened inside the agent
        (in another thread), so the API has to be able to find it afterwards.
        With `request_id` it looks up only that request's trace, so two
        simultaneous chats do not clash and one ends up seeing the other's spans.
        """
        with self._lock:
            if request_id is None:
                return self._last
            return self._per_request.get(request_id)

    def forget(self, request_id: str) -> None:
        """Remove the trace from the per-request index (the stream already has it)."""
        with self._lock:
            self._per_request.pop(request_id, None)

    # ----------------------------------------------------------------- lifecycle
    def open(
        self,
        *,
        client_id: str | None = None,
        conversation_id: str | None = None,
        endpoint: str = "(desconocido)",
    ) -> Trace:
        """Open a trace for a request."""
        model.clean_usage()  # this request's usage does not carry over the previous one's
        trace = Trace(
            client_id=client_id,
            conversation_id=conversation_id,
            endpoint=endpoint,
        )
        request_id = REQUEST_ID.get()
        with self._lock:
            self._last = trace.id
            if request_id is not None:
                # Prune by age: if the stream is cut without cleanup, the index
                # must not grow forever.
                if len(self._per_request) >= _MAX_REQUESTS:
                    for old in list(self._per_request)[:64]:
                        self._per_request.pop(old, None)
                self._per_request[request_id] = trace.id
        return trace

    def close(self, trace: Trace, status: str = "ok", detail: Any = None) -> dict:
        """Close the trace, persist it and notify through the bus."""
        trace.status = status
        trace.detail = detail
        trace.duration_ms = int((time.perf_counter() - trace.start) * 1000)
        data = trace.to_dict()
        data["costable"] = trace.costable
        self.db.record_trace(data, spans=trace.spans)
        self.bus.publish(
            trace.id,
            {
                "type": TYPE_TRACE_END,
                "id": trace.id,
                "status": status,
                "duration_ms": trace.duration_ms,
                "tokens_input": trace.tokens_input,
                "tokens_output": trace.tokens_output,
                "cost_usd": round(trace.cost_usd, 8),
                "costable": trace.costable,
                "flags": trace.flags,
            },
        )
        return data

    # ------------------------------------------------------------------- spans
    @contextmanager
    def span(
        self,
        trace: Trace,
        type: str,
        name: str,
        *,
        args: Any = None,
        detail: Any = None,
    ) -> Iterator[dict]:
        """Wrap a unit of work (node, tool, guardrail...).

        Publishes the start so the UI shows it instantly, measures the duration
        and, even if the block raises, records the span with the error.
        """
        seq = len(trace.spans)
        start = time.perf_counter()
        event_start = {
            "type": TYPE_SPAN_START,
            "trace_id": trace.id,
            "seq": seq,
            "span_type": type,
            "name": name,
            "args": args,
            "ts": time.time(),
        }
        self.bus.publish(trace.id, event_start)

        record: dict = {
            "seq": seq,
            "type": type,
            "name": name,
            "args": args,
            "result": None,
            "detail": detail,
            "duration_ms": 0,
            "status": "ok",
            "error": None,
        }
        try:
            yield record
        except Exception as e:
            record["status"] = "error"
            record["error"] = str(e)[:300]
            trace.mark(f"error_in_{name}")
            raise
        finally:
            record["duration_ms"] = int((time.perf_counter() - start) * 1000)
            record["result"] = _truncate(record["result"])
            trace.spans.append(record)
            if type == "node":
                trace.steps += 1
            if self.record is not None:
                self.record.observe_span(
                    name,
                    duration_ms=record["duration_ms"],
                    status=record["status"],
                    type=type,
                )
            self.bus.publish(
                trace.id,
                {
                    "type": TYPE_SPAN_END,
                    "trace_id": trace.id,
                    "seq": seq,
                    "span_type": type,
                    "name": name,
                    "duration_ms": record["duration_ms"],
                    "status": record["status"],
                },
            )

    def record_usage(self, trace: Trace, usage: dict | None = None) -> None:
        """Add the provider token usage to the trace and compute its cost."""
        usage = usage or model.last_usage()
        if not usage:
            return
        cost = model.cost_estimated(usage)
        trace.tokens_input += usage.get("tokens_input", 0)
        trace.tokens_output += usage.get("tokens_output", 0)
        trace.cost_usd += cost
        trace.provider = usage.get("provider") or trace.provider
        trace.model = usage.get("model") or trace.model
        if not model.is_costable(usage):
            trace.costable = False
        if self.record is not None:
            self.record.record_usage(usage, provider=trace.provider, cost_usd=cost)

    def mark(self, trace: Trace, flag: str) -> None:
        """Add a guardrail flag (without duplicates)."""
        trace.mark(flag)
        if self.record is not None:
            self.record.mark_guardrail(flag)


def _truncate(value: Any, limit: int = 600) -> Any:
    """Truncate what is stored in the trace: tool results can be long."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... (+{len(value) - limit} chars)"
    return value

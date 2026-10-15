"""The agent's metrics, in a registry that does not need Prometheus installed.

An operator must be able to answer three questions without opening the database:

  - is the agent healthy? (requests, errors, latency)
  - how much does it cost me? (tokens and dollars per provider)
  - is it leaking data or stopping attacks? (guardrails, canaries, HITL)

The output format is Prometheus' (`/metrics`), but the registry also works on
its own: `value("aurora_requests_total")` in tests and in the dashboard. If
`prometheus_client` is installed, `record_in_prometheus()` publishes the same
metrics to the global registry, so a `prometheus_client` already mounted in
the app does not see a separate island of metrics.

Client ids are **not** labels: they would leak in `/metrics` and make the
metric's cardinality unbounded. We count per endpoint, flag, tool and provider.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from settings import Settings, settings

log = logging.getLogger("aurora.metrics")

try:  # The bridge is optional: the agent has to work without it.
    from prometheus_client import Counter, Gauge

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    PROMETHEUS_AVAILABLE = False

#: Description of each metric: (help, type). HELP/TYPE lines come from here.
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "aurora_requests_total": ("Requests que entraron al agente.", "counter"),
    "aurora_turns_total": ("Turnos que llegaron a una respuesta final.", "counter"),
    "aurora_turns_waiting_approval_total": (
        "Turnos cortados esperando a un humano.",
        "counter",
    ),
    "aurora_errors_total": ("Requests que terminaron en error.", "counter"),
    "aurora_turns_ms_sum": ("Suma de duraciones de turno, en ms.", "counter"),
    "aurora_turns_ms_average": ("Duración promedio de un turno, en ms.", "gauge"),
    "aurora_spans_total": ("Spans ejecutados, por tipo y nombre.", "counter"),
    "aurora_spans_error_total": ("Spans que fallaron, por tipo y nombre.", "counter"),
    "aurora_spans_ms_sum": ("Suma de duraciones de span, en ms.", "counter"),
    "aurora_tokens_total": ("Tokens consumidos, por proveedor.", "counter"),
    "aurora_tokens_input_total": ("Tokens de entrada, por proveedor.", "counter"),
    "aurora_cost_usd_total": ("Dólares gastados, por proveedor.", "counter"),
    "aurora_guardrails_total": ("Guardrails que dispararon, por flag.", "counter"),
    "aurora_canaries_blocked_total": (
        "Intentos de filtrar el prompt interno que se frenaron.",
        "counter",
    ),
    "aurora_approvals_total": ("Decisiones humanas, por resultado.", "counter"),
}


class Record:
    """In-memory counters with Prometheus text output."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauge: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- primitives
    def sum(self, metric: str, quantity: float = 1, **labels: str) -> None:
        """Add to a counter. Labels are sorted so series are not duplicated."""
        key = (metric, self._labels(labels))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + quantity

    def set(self, metric: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauge[(metric, self._labels(labels))] = float(value)

    def value(self, metric: str, **labels: str) -> float:
        """The value of a series.

        With labels it looks up that exact series; without them it sums every
        series with that name (which is what you ask in the dashboard: "how
        many turns were there", not "how many on /chat").
        """
        key = (metric, self._labels(labels))
        with self._lock:
            if key in self._values or key in self._gauge:
                return self._values.get(key, self._gauge.get(key, 0.0))
            return sum(
                value
                for (name, _labels), value in {**self._values, **self._gauge}.items()
                if name == metric
            )

    def observed(self) -> dict[str, float]:
        """Sum of each metric, without breaking down by label.

        This is what the dashboard needs: "320 requests so far today", not the
        per-endpoint detail. Labeled series are read with `value()`.
        """
        totals: dict[str, float] = {}
        with self._lock:
            series = {**self._values, **self._gauge}
        for (name, _labels), value in series.items():
            totals[name] = totals.get(name, 0.0) + value
        return totals

    @staticmethod
    def _labels(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((k, str(v)) for k, v in labels.items() if v is not None))

    # ------------------------------------------------------------------ agent
    def start(self, *, client_id: str | None = None, endpoint: str = "(desconocido)") -> dict:
        """Open the count for a request. Returns the timer for `close`.

        The request is counted on the way in, not on the way out: if the process
        dies mid-turn, it must still show up in the dashboard.
        """
        self.sum("aurora_requests_total", endpoint=endpoint)
        return {"endpoint": endpoint, "start": time.perf_counter()}

    def close(self, timer: dict, *, status: str = "ok") -> None:
        """Close the request: classify the outcome and add its latency."""
        endpoint = timer.get("endpoint", "(desconocido)")
        if status == "waiting_approval":
            # The turn did not finish: it is measured separately, so the SLA is not misrepresented.
            self.sum("aurora_turns_waiting_approval_total", endpoint=endpoint)
        elif status == "error":
            self.sum("aurora_errors_total", endpoint=endpoint)
        else:
            self.sum("aurora_turns_total", endpoint=endpoint)
        self._latency(timer, endpoint)

    def error(self, timer: dict, *, error: str | None = None) -> None:
        """The request broke: count the error and leave the reason in the log."""
        endpoint = timer.get("endpoint", "(desconocido)")
        self.sum("aurora_errors_total", endpoint=endpoint)
        self._latency(timer, endpoint)
        log.warning("request falló en %s: %s", endpoint, error or "sin detalle")

    def _latency(self, timer: dict, endpoint: str) -> None:
        duration = (time.perf_counter() - timer.get("start", time.perf_counter())) * 1000
        self.sum("aurora_turns_ms_sum", duration, endpoint=endpoint)
        self.set("aurora_turns_ms_average", self.average_turn())

    def average_turn(self) -> float:
        """Mean latency of closed turns."""
        total = self.value("aurora_turns_ms_sum")
        turns = (
            self.value("aurora_turns_total")
            + self.value("aurora_turns_waiting_approval_total")
        )
        return total / turns if turns else 0.0

    def observe_span(
        self, name: str, *, duration_ms: float, status: str = "ok", type: str = "tool"
    ) -> None:
        """A unit of work finished: count it and add its latency."""
        self.sum("aurora_spans_total", type=type, name=name)
        self.sum("aurora_spans_ms_sum", duration_ms, type=type, name=name)
        if status == "error":
            self.sum("aurora_spans_error_total", type=type, name=name)

    def record_usage(
        self,
        usage: dict | None,
        *,
        provider: str | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Tokens and money per provider (which is where the bill is decided)."""
        if not usage:
            return
        label = {"provider": provider or usage.get("provider") or "(desconocido)"}
        self.sum("aurora_tokens_total", usage.get("tokens_total", 0), **label)
        self.sum("aurora_tokens_input_total", usage.get("tokens_input", 0), **label)
        self.sum(
            "aurora_cost_usd_total",
            cost_usd if cost_usd is not None else usage.get("cost_usd", 0.0),
            **label,
        )

    # -------------------------------------------------------------- guardrails
    def mark_guardrail(self, flag: str, **labels: str) -> None:
        self.sum("aurora_guardrails_total", 1, flag=flag, **labels)
        if flag == "canary_exposed":
            self.sum("aurora_canaries_blocked_total")

    def human_decision(self, *, approved: bool) -> None:
        self.sum("aurora_approvals_total", 1, decision="approved" if approved else "rejected")

    # ----------------------------------------------------------------- reads
    def spend(self, provider: str | None = None) -> float:
        """Dollars spent, in total or per provider."""
        if provider:
            return self.value("aurora_cost_usd_total", provider=provider)
        with self._lock:
            return sum(
                value
                for (name, labels), value in self._values.items()
                if name == "aurora_cost_usd_total"
            )

    def retention_days(self) -> int:
        return self.config.RETENTION_DAYS

    # ------------------------------------------------------------------ output
    def render(self) -> str:
        """The text that Prometheus (or a `curl`) reads at `/metrics`."""
        lines: list[str] = []
        with self._lock:
            series = sorted({**self._values, **self._gauge}.items())
        seen: set[str] = set()
        for (name, labels), value in series:
            if name not in DESCRIPTIONS:
                continue
            if name not in seen:
                help, type = DESCRIPTIONS[name]
                lines.append(f"# HELP {name} {help}")
                lines.append(f"# TYPE {name} {type}")
                seen.add(name)
            labels_text = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
            suffix = f"{{{labels_text}}}" if labels_text else ""
            lines.append(f"{name}{suffix} {value}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    """Prometheus does not tolerate `\\`, `"` or newlines in a label value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def record_in_prometheus(record: Record, prefix: str = "aurora") -> None:
    """Publish the record to the `prometheus_client` registry, if available.

    Called once when the app is built. The series are created here; the values
    are refreshed on every `export()`, which the `/metrics` endpoint invokes.
    """
    if not PROMETHEUS_AVAILABLE:  # pragma: no cover
        log.info("prometheus_client no está instalado: se sirve /metrics con el registro local")
        return
    from prometheus_client import REGISTRY

    existing = getattr(REGISTRY, "_names_to_collectors", {})
    for name, (help, type) in DESCRIPTIONS.items():
        if name in existing:
            continue
        factory = Counter if type == "counter" else Gauge
        factory(name, help, registry=REGISTRY)
    log.info("métricas publicadas en el registry de prometheus_client")


def export(record: Record) -> None:
    """Push the local record's values to the `prometheus_client` registry."""
    if not PROMETHEUS_AVAILABLE:  # pragma: no cover
        return
    from prometheus_client import REGISTRY

    for (name, labels), value in list(record._values.items()) + list(
        record._gauge.items()
    ):
        collector = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        if collector is None:  # pragma: no cover
            continue
        try:
            collector.labels(**dict(labels))._value.set(float(value))
        except (AttributeError, ValueError):  # pragma: no cover - third-party API
            log.debug("no se pudo actualizar %s%r en prometheus", name, labels)


def summary(record: Record) -> dict[str, Any]:
    """The same numbers as JSON, for the dashboard and for a health check."""
    return {
        "requests": record.value("aurora_requests_total"),
        "turns": record.value("aurora_turns_total"),
        "waiting_approval": record.value("aurora_turns_waiting_approval_total"),
        "errors": record.value("aurora_errors_total"),
        "turn_ms_average": round(record.average_turn(), 1),
        "spend_usd": round(record.spend(), 6),
        "canaries_blocked": record.value("aurora_canaries_blocked_total"),
    }

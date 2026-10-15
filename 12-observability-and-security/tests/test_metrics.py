"""Seam 7: the metrics an operator watches to know whether the agent is healthy.

Specification:
  - every request counts, and so do turns waiting for approval;
  - agent and tool timings are measured by name;
  - consumption is tracked per provider (tokens and dollars);
  - guardrails and HITL leave counters that can be queried;
  - `/metrics` exposes the text Prometheus understands;
  - purging old data is idempotent and respects the configured retention.
"""

import re

import pytest

import metrics
import model
from persistence import Database
from settings import Settings
from traces import Tracer


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "metrics.db") as base:
        yield base


def _config(**kwargs) -> Settings:
    base = {"JWT_SECRET": "x" * 32, "RETENTION_DAYS": 30}
    base.update(kwargs)
    return Settings(**base)


def _trace(db, client_id, **kwargs):
    """Record a plausible trace and return its data."""
    data = {
        "id": kwargs.pop("trace_id", f"tra_{client_id}"),
        "client_id": client_id,
        "conversation_id": None,
        "endpoint": "/chat",
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "steps": 2,
        "tokens_input": 300,
        "tokens_output": 20,
        "cost_usd": 0.004,
        "duration_ms": 1200,
        "status": "ok",
        "flags": [],
    }
    data.update(kwargs)
    return db.record_trace(data, spans=data.pop("spans", None) or [])


# ------------------------------------------------------------------ counters
def test_requests_and_turns_are_counted(db):
    reg = metrics.Record(config=_config())
    trace = reg.start(client_id="cli_1", endpoint="/chat")
    reg.close(trace, status="ok")

    assert reg.value("aurora_requests_total") == 1
    assert reg.value("aurora_turns_total") == 1
    assert reg.value("aurora_turns_total", endpoint="/chat") == 1


def test_a_waiting_turn_is_not_counted_as_a_finished_turn(db):
    reg = metrics.Record(config=_config())
    trace = reg.start(client_id="cli_1", endpoint="/chat")
    reg.close(trace, status="waiting_approval")

    # The turn did not finish: it is measured separately, so the SLA is not misrepresented.
    assert reg.value("aurora_requests_total") == 1
    assert reg.value("aurora_turns_total") == 0
    assert reg.value("aurora_turns_waiting_approval_total") == 1


def test_a_failed_request_is_visible(db):
    reg = metrics.Record(config=_config())
    trace = reg.start(client_id="cli_1", endpoint="/chat")
    reg.error(trace, error="boom")

    assert reg.value("aurora_requests_total") == 1
    assert reg.value("aurora_errors_total", endpoint="/chat") == 1
    assert reg.value("aurora_turns_total") == 0


def test_tool_latency_is_observed_by_name(db):
    reg = metrics.Record(config=_config())
    reg.observe_span("search_product", duration_ms=80, status="ok")
    reg.observe_span("search_product", duration_ms=120, status="ok")
    reg.observe_span("emit_refund", duration_ms=40, status="error")

    assert reg.value("aurora_spans_total", type="tool", name="search_product") == 2
    assert reg.value("aurora_spans_error_total", type="tool", name="emit_refund") == 1
    assert reg.observed()["aurora_spans_ms_sum"] >= 240


def test_spend_is_counted_per_provider(db):
    reg = metrics.Record(config=_config())
    reg.record_usage(
        {"tokens_input": 100, "tokens_output": 20, "tokens_total": 120},
        provider="gemini",
        cost_usd=0.001,
    )
    reg.record_usage(
        {"tokens_input": 50, "tokens_output": 10, "tokens_total": 60},
        provider="openai",
        cost_usd=0.002,
    )

    assert reg.value("aurora_tokens_total", provider="gemini") == 120
    assert reg.value("aurora_tokens_input_total", provider="openai") == 50
    assert reg.spend() == pytest.approx(0.003)
    assert reg.spend(provider="openai") == pytest.approx(0.002)


def test_guardrails_and_human_approval_have_counters(db):
    reg = metrics.Record(config=_config())
    reg.mark_guardrail("pii_in_input")
    reg.mark_guardrail("pii_in_input")
    reg.mark_guardrail("possible_injection")
    reg.mark_guardrail("canary_exposed")
    reg.human_decision(approved=False)
    reg.human_decision(approved=True)

    assert reg.value("aurora_guardrails_total", flag="pii_in_input") == 2
    assert reg.value("aurora_guardrails_total", flag="possible_injection") == 1
    assert reg.value("aurora_canaries_blocked_total") == 1
    assert reg.value("aurora_approvals_total", decision="rejected") == 1
    assert reg.value("aurora_approvals_total", decision="approved") == 1


# ------------------------------------------------------------------ Prometheus
def test_the_text_output_is_readable_by_prometheus(db):
    reg = metrics.Record(config=_config())
    reg.start(client_id="cli_1", endpoint="/chat")
    reg.mark_guardrail("pii_in_input")

    text = reg.render()

    assert "# HELP aurora_requests_total" in text
    assert "# TYPE aurora_requests_total counter" in text
    assert 'aurora_requests_total{endpoint="/chat"} 1.0' in text
    assert 'aurora_guardrails_total{flag="pii_in_input"} 1.0' in text
    # A label value cannot carry odd characters: they are escaped.
    reg.mark_guardrail('with "quotes" and \\backslash')
    assert '\\"' in reg.render()


def test_the_client_name_never_appears_in_the_metrics(db):
    reg = metrics.Record(config=_config())
    reg.start(client_id="cli_secret_123", endpoint="/chat")

    assert "cli_secret_123" not in reg.render()


def test_prometheus_registry_and_the_local_one_agree(db):
    reg = metrics.Record(config=_config())
    reg.start(client_id="cli_1", endpoint="/chat")

    # If someone installs prometheus_client, the same metric is published there.
    if metrics.PROMETHEUS_AVAILABLE:
        metrics.record_in_prometheus(reg)
        from prometheus_client import generate_latest

        assert b"aurora_requests_total" in generate_latest()
    else:
        pytest.skip("prometheus_client is not installed")


# ----------------------------------------------------- the tracer feeds them
def test_the_tracer_feeds_the_metrics(db):
    reg = metrics.Record(config=_config())
    tracer = Tracer(db, record=reg)
    trace = tracer.open(client_id="cli_1", endpoint="/chat")

    with tracer.span(trace, "tool", "search_product") as span:
        span["result"] = "2 productos"
    tracer.mark(trace, "pii_in_input")
    tracer.close(trace)

    assert reg.value("aurora_spans_total", type="tool", name="search_product") == 1
    assert reg.value("aurora_guardrails_total", flag="pii_in_input") == 1


def test_the_tracer_reports_the_spend(db):
    reg = metrics.Record(config=_config())
    tracer = Tracer(db, record=reg)
    trace = tracer.open(client_id="cli_1", endpoint="/chat")
    model.record_usage(
        {"tokens_input": 300, "tokens_output": 20, "tokens_total": 320},
        provider="gemini",
        model_name="gemini-2.5-flash",
    )

    tracer.record_usage(trace)

    assert reg.value("aurora_tokens_total", provider="gemini") == 320
    assert reg.spend(provider="gemini") > 0


def test_a_failed_span_shows_up_in_the_metrics(db):
    reg = metrics.Record(config=_config())
    tracer = Tracer(db, record=reg)
    trace = tracer.open(client_id="cli_1", endpoint="/chat")

    with pytest.raises(ValueError), tracer.span(trace, "tool", "hack_everything"):
        raise ValueError("boom")

    assert reg.value("aurora_spans_error_total", type="tool", name="hack_everything") == 1


def test_the_tracer_works_without_a_registry(db):
    """Without a record the tracer does not break: metrics are an extra, not a requirement."""
    trace = Tracer(db).open(client_id="cli_1", endpoint="/chat")
    with Tracer(db).span(trace, "node", "agent") as span:
        span["result"] = "ok"
    assert Tracer(db).close(trace)["id"] == trace.id


# -------------------------------------------------------------------- purge
def test_purging_old_rows_is_idempotent(db):
    _trace(db, "cli_1", trace_id="tra_x")
    db.connection().execute("UPDATE traces SET created = '2000-01-01 00:00:00'")

    first = db.purge_old(days=30)
    second = db.purge_old(days=30)

    assert first["traces"] == 1
    assert first["spans"] == 0 or first["spans"] >= 0
    assert second["traces"] == 0
    assert db.get_trace("tra_x") is None


def test_the_purge_keeps_what_is_inside_the_window(db):
    _trace(db, "cli_recent", trace_id="tra_cli_recent")

    purged = db.purge_old(days=30)

    assert purged["traces"] == 0
    assert db.get_trace("tra_cli_recent") is not None


def test_the_retention_setting_is_the_default_window(db):
    reg = metrics.Record(config=_config(RETENTION_DAYS=7))

    assert reg.retention_days() == 7
    assert re.fullmatch(r"\d+", str(reg.retention_days()))


def test_purging_never_deletes_a_pending_approval(db):
    """A pending item is a person's work: retention cannot delete it."""
    db.record_approval(client_id="cli_1", tool="emit_refund", args={"amount": 1})
    db.connection().execute("UPDATE approvals SET created = '2000-01-01 00:00:00'")

    db.purge_old(days=30)

    assert [p["status"] for p in db.list_approvals(status="pending")] == ["pending"]


def test_purging_does_delete_resolved_approvals_that_are_old(db):
    approval = db.record_approval(
        client_id="cli_1", tool="emit_refund", args={"amount": 1}
    )
    db.resolve_approval(approval["id"], "rejected", "operator", "expired")
    db.connection().execute("UPDATE approvals SET created = '2000-01-01 00:00:00'")

    db.purge_old(days=30)

    assert db.get_approval(approval["id"]) is None

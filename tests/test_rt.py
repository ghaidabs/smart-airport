"""Tests du cadre temps réel (P3). Les marges restent larges pour éviter les faux échecs."""

import json
import time

import pytest

from rt.metrics import JobRecord, MetricsCollector, summarize
from rt.monitors import (FlightMonitor, FlightTable, QueueMonitor, SecurityAlert,
                         make_message, reference_flights)
from rt.periodic import PeriodicTask
from rt.sporadic import QueueEventSource, SporadicTask
from rt.tasks import TASKS_V0, load_task_table, total_utilization


def run_periodic(body, jobs, period_ms=10.0, deadline_ms=10.0, **kwargs):
    metrics = MetricsCollector()
    task = PeriodicTask("T", period_ms, deadline_ms, body, metrics=metrics,
                        max_jobs=jobs, **kwargs)
    task.start()
    task.join(timeout=10)
    assert not task.is_alive()
    return metrics.records("T")


def test_job_record_derives_timing():
    job = JobRecord("T", 0, 0, 2_000_000, 7_000_000, 6_000_000)
    assert (job.jitter_ms, job.c_ms, job.response_ms) == (2.0, 5.0, 7.0)
    assert job.missed


def test_deadline_met_at_exact_deadline():
    assert not JobRecord("T", 0, 0, 0, 5, 5).missed


def test_summary_counts_misses():
    rows = summarize([JobRecord("T", 0, 0, 0, 2_000_000, 5_000_000),
                      JobRecord("T", 1, 0, 0, 8_000_000, 5_000_000)])
    assert rows[0]["misses"] == 1
    assert rows[0]["c_max_ms"] == 8.0


def test_periodic_releases_are_absolute_without_drift():
    records = run_periodic(lambda: time.sleep(0.002), 20, period_ms=20.0, deadline_ms=20.0)
    origin = records[0].release_ns
    assert [r.release_ns - origin for r in records] == [k * 20_000_000 for k in range(20)]


def test_skip_policy_skips_passed_releases():
    calls = []

    def body():
        calls.append(1)
        if len(calls) == 1:
            time.sleep(0.035)

    records = run_periodic(body, 3, overrun_policy="skip")
    assert records[0].missed
    assert records[0].skipped_after >= 3
    gap = records[1].release_ns - records[0].release_ns
    assert gap == (records[0].skipped_after + 1) * 10_000_000


def test_catch_up_policy_keeps_every_release():
    calls = []

    def body():
        calls.append(1)
        if len(calls) == 1:
            time.sleep(0.035)

    records = run_periodic(body, 5, overrun_policy="catch_up")
    assert [r.release_ns - records[0].release_ns for r in records] == [
        k * 10_000_000 for k in range(5)]
    assert all(r.skipped_after == 0 for r in records)


def test_periodic_survives_body_error():
    calls = []

    def body():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("panne simulée")

    records = run_periodic(body, 3, period_ms=20.0, deadline_ms=20.0)
    assert [r.error for r in records] == [True, False, False]


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        PeriodicTask("T", 10, 10, lambda: None, overrun_policy="unknown")


def wait_for(predicate, timeout=5.0):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_sporadic_enforces_min_interarrival_and_keeps_events():
    source = QueueEventSource()
    metrics = MetricsCollector()
    task = SporadicTask("S", 30.0, 500.0, lambda _: None, source, metrics=metrics)
    task.start()
    for index in range(4):
        source.put(make_message("X", "a", "b", f"evt-{index}", {}))
    assert wait_for(lambda: len(metrics.records("S")) == 4)
    task.stop()
    task.join(timeout=2)
    records = metrics.records("S")
    assert [r.corr_id for r in records] == [f"evt-{i}" for i in range(4)]
    gaps = [b.start_ns - a.start_ns for a, b in zip(records, records[1:])]
    assert min(gaps) >= 30_000_000


def test_sporadic_response_counts_time_since_event():
    source = QueueEventSource()
    metrics = MetricsCollector()
    message = make_message("X", "a", "b", "evt", {})
    time.sleep(0.02)
    source.put(message)
    task = SporadicTask("S", 10.0, 5.0, lambda _: None, source, metrics=metrics)
    task.start()
    assert wait_for(lambda: len(metrics.records("S")) == 1)
    task.stop()
    task.join(timeout=2)
    record = metrics.records("S")[0]
    assert record.response_ms >= 19.0
    assert record.missed


def test_flight_monitor_detects_delay_once():
    sent = []
    table = FlightTable(reference_flights())
    monitor = FlightMonitor(table, lambda dest, msg: sent.append((dest, msg)))
    table.set_delay("TU701", 20, "evt-0001")
    monitor()
    monitor()
    assert len(sent) == 1
    dest, msg = sent[0]
    assert dest == "ai" and msg["type"] == "RISK_REQUEST" and msg["corr_id"] == "evt-0001"
    features = msg["payload"]["flights"][0]["features"]
    assert features["delay_min"] == 20 and features["slack_min"] == 55
    assert set(features) == {"delay_min", "slack_min", "gate_distance_m", "security_wait_min",
                             "n_connecting", "pax_total", "hour", "gate_changed"}


def test_flight_monitor_threshold_is_strict():
    sent = []
    table = FlightTable(reference_flights())
    monitor = FlightMonitor(table, lambda dest, msg: sent.append(msg))
    table.set_delay("TU701", 5, "evt-1")
    monitor()
    assert not sent


def test_flight_monitor_detects_gate_change():
    sent = []
    table = FlightTable(reference_flights())
    monitor = FlightMonitor(table, lambda dest, msg: sent.append(msg))
    table.set_gate("TU701", "G15", "evt-2", 200)
    monitor()
    assert sent[0]["payload"]["flights"][0]["features"]["gate_changed"] == 1


def test_queue_monitor_alert_crossing_and_rearm():
    alerts = []
    monitor = QueueMonitor(alerts.append, arrivals_per_min=0)
    monitor.set_queue(100)
    monitor()
    assert not alerts
    monitor.set_queue(120, "evt-3")
    monitor()
    assert alerts[0]["payload"] == {"queue_len": 120, "wait_min": 12.0}
    monitor.set_queue(125)
    monitor()
    assert len(alerts) == 1
    monitor.set_queue(150)
    monitor()
    assert len(alerts) == 2
    monitor.set_queue(50)
    monitor()
    monitor.set_queue(120)
    monitor()
    assert len(alerts) == 3


def test_security_alert_forwards_to_decision():
    sent = []
    alert = SecurityAlert(lambda dest, msg: sent.append((dest, msg)))
    alert(make_message("SECURITY_ALERT", "passenger", "passenger", "evt-4",
                       {"queue_len": 120, "wait_min": 12.0}))
    dest, msg = sent[0]
    assert dest == "decision" and msg["corr_id"] == "evt-4"
    assert msg["payload"] == {"queue_len": 120, "wait_min": 12.0}


def test_v0_utilization_matches_guide():
    assert total_utilization(TASKS_V0) == pytest.approx(0.51)


def test_measured_values_override_v0(tmp_path):
    path = tmp_path / "wcet.json"
    path.write_text(json.dumps({"tasks": {"T1": {"c_ms": 0.5}}}), encoding="utf-8")
    table = {t.task_id: t for t in load_task_table(path)}
    assert table["T1"].c_ms == 0.5 and table["T1"].c_source == "mesuré"
    assert table["T3"].c_ms == 20.0 and table["T3"].c_source == "v0"

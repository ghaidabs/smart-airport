"""Branchement des tâches T1, T2, T3, T4 et T5 dans les nœuds réels (P3)."""

import csv
import time

import pytest

from common import config
from common.protocol import Message, MessageType as T
from nodes.decision_node import DecisionNode
from nodes.flight_node import FlightNode, synthetic_flight
from nodes.passenger_node import PassengerNode
from rt.monitors import FlightMonitor, FlightTable, QueueMonitor, reference_flights
from rt.report import read_jobs
from rt.runtime import NodeRuntime, net_message


def monitor():
    sent = []
    table = FlightTable(reference_flights())
    return table, FlightMonitor(table, lambda _dest, msg: sent.append(msg), net_message), sent


def test_reference_flights_match_shared_config():
    for record in reference_flights():
        f = config.FLIGHTS[record.flight]
        assert record.slack_min == f["slack_min"]
        assert record.gate_distance_m == config.gate_distance(f["gate"], f["connection_gate"])


def test_t1_builds_a_network_message_with_all_flights_context_and_t0():
    table, t1, sent = monitor()
    table.set_delay("TU701", 20, "evt-1", 123)
    t1()
    msg = sent[0]
    assert isinstance(msg, Message) and msg.corr_id == "evt-1" and msg.payload["t0_ns"] == 123
    assert {f["flight"] for f in msg.payload["flights"]} == {"TU701", "TU702", "TU703"}
    assert msg.payload["flights"][0]["context"] == {"gate": "G12", "connection_gate": "G03"}


def test_t1_merges_events_of_one_tick_and_keeps_oldest_cause():
    table, t1, sent = monitor()
    table.set_delay("TU701", 20, "evt-1", 100)
    table.set_gate("TU701", None, "evt-2", t0_ns=200)
    table.set_security_wait(12.0, "evt-3", 300)
    t1()
    assert len(sent) == 1
    payload = sent[0].payload
    assert sent[0].corr_id == "evt-1" and payload["t0_ns"] == 100
    assert payload["merged_corr_ids"] == ["evt-2", "evt-3"]
    features = payload["flights"][0]["features"]
    assert features["gate_changed"] == 1 and features["security_wait_min"] == 12.0


def test_security_wait_triggers_t1_only_during_a_disturbance():
    table, t1, sent = monitor()
    table.set_security_wait(12.0, "evt-1", 100)
    t1()
    assert not sent
    table.set_delay("TU701", 20, "evt-2", 200)
    t1()
    table.set_security_wait(15.0, "evt-3", 300)
    t1()
    assert [m.corr_id for m in sent] == ["evt-2", "evt-3"]


def test_system_gate_assignment_does_not_create_a_prediction_loop():
    table, t1, sent = monitor()
    table.set_gate("TU701", None, "evt-1", t0_ns=100)
    t1()
    table.assign_gate("TU701", "G15", 700)
    t1()
    assert len(sent) == 1
    assert table.get("TU701").features()["gate_changed"] == 1


def test_synthetic_flight_first_gate_is_not_a_gate_change():
    table = FlightTable(reference_flights())
    table.set_security_wait(7.0)
    table.add(synthetic_flight("TU801"))
    assert table.get("TU801").security_wait_min == 7.0
    table.assign_gate("TU801", "G15", 700)
    assert table.get("TU801").features()["gate_changed"] == 0


def test_queue_update_alerts_immediately_without_duplicate_from_t2():
    alerts = []
    queue = QueueMonitor(alerts.append, message_factory=net_message, arrivals_per_min=0)
    queue.set_queue(120, "evt-1", 123)
    queue.check_alert()
    queue()
    assert len(alerts) == 1
    assert alerts[0].corr_id == "evt-1" and alerts[0].ts_ns == 123
    assert alerts[0].payload["t0_ns"] == 123


def test_t2_publishes_simulated_wait_only_after_a_real_change():
    waits = []
    queue = QueueMonitor(lambda _: None, arrivals_per_min=0, on_wait_change=waits.append)
    queue.set_queue(50)
    for _ in range(3):
        queue()  # 5 personnes servies par pas, soit 0,5 min d'attente
    assert not waits
    queue()
    assert waits == [pytest.approx(3.0)]


@pytest.fixture()
def flight_node(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    node = FlightNode(port=0)
    node.setup()
    node.replies = []
    node.reply = lambda cause, t, dest, payload: node.replies.append((t, dest, payload))
    yield node
    node.log.close()


def event(msg_type, dest, payload):
    return Message(msg_type, "injector", dest, payload | {"t0_ns": time.time_ns()})


def test_flight_node_reassigns_lost_gate_and_keeps_shared_state_consistent(flight_node):
    flight_node.on_gate_unavailable(event(T.GATE_UNAVAILABLE, "flight", {"gate": "G12"}))
    assert flight_node.table.get("TU701").gate is None
    flight_node.on_gate_request(Message(T.GATE_REQUEST, "decision", "flight", {"flight": "TU701"}))
    assert flight_node.replies[0][2] == {"flight": "TU701", "gate": "G15"}
    record = flight_node.table.get("TU701")
    assert (record.gate, record.gate_distance_m) == ("G15", 700)
    assert flight_node.gates["G12"] == "DOWN" and flight_node.gates["G15"] == "TU701"


def test_flight_node_accepts_unknown_synthetic_flight(flight_node):
    flight_node.on_delay(event(T.FLIGHT_DELAY, "flight", {"flight": "TU801", "delay_min": 25}))
    assert flight_node.table.get("TU801").delay_min == 25


def test_passenger_queue_update_feeds_ai_inputs_and_activates_t5(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    node = PassengerNode(port=0)
    node.setup()
    replies = []
    node.reply = lambda cause, t, dest, payload: replies.append((t, dest, payload))
    node.on_queue(event(T.QUEUE_UPDATE, "passenger", {"queue_len": 120}))
    alert = node.t5_events.get(timeout=1)
    node.log.close()
    assert replies == [(T.QUEUE_UPDATE, "flight", {"queue_len": 120, "wait_min": 12.0})]
    assert alert.type == T.SECURITY_ALERT and alert.payload["queue_len"] == 120


def test_decision_dispatch_records_t4_job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    node = DecisionNode(port=0)
    node.setup()
    node.broadcast = lambda *_: None
    msg = Message(T.RISK_RESULT, "ai", "decision", {"flight": "TU702", "risk": 0.1}).stamp()
    node.dispatch(msg)
    node.rt.stop()
    node.log.close()
    jobs = read_jobs(tmp_path / "rt")
    assert [(j.task, j.corr_id, j.error) for j in jobs] == [("T4", msg.corr_id, False)]


def test_runtime_writes_jobs_outside_timeline_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    node = DecisionNode(port=0)
    runtime = NodeRuntime("decision", node.log)
    runtime.run_job("T4", Message(T.PAX_DONE, "passenger", "decision").stamp(), lambda: None)
    runtime.stop()
    node.log.close()
    assert not list(tmp_path.glob("*_jobs.csv"))
    with (tmp_path / "rt" / "decision_jobs.csv").open(newline="", encoding="utf-8") as f:
        assert next(csv.DictReader(f))["task"] == "T4"

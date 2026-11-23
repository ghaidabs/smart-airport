import csv
import queue
import threading
import time

import pytest

import ai.predictor as predictor_module
from ai.generate_data import generate
from ai.predictor import DEMO_FLIGHTS
from ai.train import train
from common import config
from common.logger import CsvLogger
from common.net import NodeServer, Sender
from common.protocol import Message, MessageType as T
from nodes.ai_node import AINodeService

CORR = "evt-test-0001"
T0_NS = 1_700_000_000_000_000_000


@pytest.fixture()
def system(tmp_path, monkeypatch):
    """Nœud IA réel (port libre) + faux nœud Décision qui reçoit ses résultats."""
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    csv_path = tmp_path / "data.csv"
    generate(6000, seed=42).to_csv(csv_path, index=False)
    train(csv_path, tmp_path / "models", report_dir=None, version="risk-v1")
    monkeypatch.setattr(predictor_module, "DEFAULT_MODEL_DIR", tmp_path / "models")

    decision_inbox: queue.Queue = queue.Queue()
    decision_log, client_log = CsvLogger("decision_test"), CsvLogger("flight_test")
    decision = NodeServer("decision", decision_log, decision_inbox, port=0).start()

    node = AINodeService(port=0)
    node.server.start()
    node.setup()
    node.sender = Sender(node.log, {"decision": ("127.0.0.1", decision.port)})
    node.rt.start()  # T3 traite les RISK_REQUEST dans son propre thread

    stop = threading.Event()

    def loop() -> None:  # équivalent de BaseNode.run() sans les signaux (thread secondaire)
        while not stop.is_set():
            msg = node._next(0.05)
            if msg is not None:
                node.dispatch(msg)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    client = Sender(client_log, {"ai": ("127.0.0.1", node.server.port)})
    try:
        yield node, client, decision_inbox, tmp_path
    finally:
        stop.set()
        thread.join(timeout=2)
        node.rt.stop(write=False)
        client.close()
        node.sender.close()
        node.server.stop()
        decision.stop()
        for log in (node.log, decision_log, client_log):  # Windows garde les fichiers verrouillés
            log.close()


def request(flights, corr_id=CORR):
    payload = {
        "t0_ns": T0_NS,
        "flights": [
            {"flight": f, "features": DEMO_FLIGHTS[f], "context": {"gate": "G12", "connection_gate": "G03"}}
            for f in flights
        ],
    }
    return Message(type=T.RISK_REQUEST, source="flight", dest="ai", payload=payload, corr_id=corr_id)


def collect(inbox, n, timeout=3.0):
    out, deadline = [], time.time() + timeout
    while len(out) < n and time.time() < deadline:
        try:
            out.append(inbox.get(timeout=0.1))
        except queue.Empty:
            pass
    return out


def test_real_tcp_request_gives_three_results_in_risk_order(system):
    _, client, inbox, _ = system
    assert client.send("ai", request(["TU701", "TU702", "TU703"])) is True
    results = collect(inbox, 3)
    assert [m.payload["flight"] for m in results] == ["TU701", "TU703", "TU702"]
    for m in results:
        assert (m.type, m.source, m.dest, m.corr_id) == (T.RISK_RESULT, "ai", "decision", CORR)
        assert m.payload["t0_ns"] == T0_NS                     # latence de bout en bout conservée
        assert m.payload["features"] == DEMO_FLIGHTS[m.payload["flight"]]
        assert m.payload["context"] == {"gate": "G12", "connection_gate": "G03"}
        assert m.payload["model_version"] == "risk-v1" and m.payload["inference_ms"] > 0
    assert results[0].payload["risk"] >= 0.7 > results[2].payload["risk"]


def test_journal_csv_has_ai_events_with_corr_id(system):
    node, client, inbox, tmp_path = system
    client.send("ai", request(["TU701"]))
    collect(inbox, 1)
    node.log._file.flush()
    rows = list(csv.DictReader((tmp_path / "logs" / "ai.csv").open(encoding="utf-8")))
    events = {(r["event"], r["corr_id"]) for r in rows}
    assert ("RECV", CORR) in events and ("RISK_RESULT", CORR) in events and ("SEND", CORR) in events


def test_node_survives_bad_request_then_serves_next(system):
    _, client, inbox, _ = system
    bad = Message(type=T.RISK_REQUEST, source="flight", dest="ai", payload={"flights": 42}, corr_id="evt-test-bad")
    client.send("ai", bad)
    client.send("ai", request(["TU701"], corr_id="evt-test-good"))
    results = collect(inbox, 1)
    assert len(results) == 1 and results[0].corr_id == "evt-test-good"

import queue
import threading
import time

import pytest

from ai.generate_data import generate
from ai.predictor import DEMO_FLIGHTS, RiskPredictor
from ai.retrain_cloud import next_version, retrain_once
from ai.train import train
from nodes.ai_node import AINode


@pytest.fixture()
def model_dir(tmp_path):
    csv = tmp_path / "data.csv"
    generate(6000, seed=42).to_csv(csv, index=False)
    train(csv, tmp_path, report_dir=None, version="risk-v1")
    return tmp_path


def make_request(corr_id="evt-0001", flights=None, ts_ns=None):
    flights = flights or list(DEMO_FLIGHTS)
    return {
        "id": "x", "type": "RISK_REQUEST", "source": "flight", "dest": "ai",
        "ts_ns": ts_ns or time.time_ns(), "corr_id": corr_id,
        "payload": {"flights": [{"flight": f, "features": DEMO_FLIGHTS[f]} for f in flights]},
    }


@pytest.fixture()
def node(model_dir):
    sent, logs = [], []
    n = AINode(RiskPredictor(model_dir), sent.append, lambda e, d, c: logs.append((e, d, c)))
    return n, sent, logs


def test_one_result_per_flight_sorted_by_risk_with_annex_fields(node):
    n, sent, _ = node
    assert n.handle(make_request()) == 3
    assert [m["payload"]["flight"] for m in sent] == ["TU701", "TU703", "TU702"]
    for m in sent:
        assert m["type"] == "RISK_RESULT" and m["source"] == "ai" and m["dest"] == "decision"
        assert m["corr_id"] == "evt-0001" and isinstance(m["ts_ns"], int) and m["id"]
        assert set(m["payload"]) == {"flight", "risk", "model_version", "inference_ms"}
    assert sent[0]["payload"]["risk"] >= 0.7


def test_ignores_other_message_types(node):
    n, sent, logs = node
    assert n.handle({"type": "QUEUE_UPDATE", "corr_id": "c"}) == 0
    assert not sent and logs[0][0] == "IGNORED"


def test_bad_items_skipped_good_ones_served(node):
    n, sent, logs = node
    req = make_request(flights=["TU701"])
    req["payload"]["flights"] += [
        {"flight": "TU999", "features": {"delay_min": 5}},   # variables manquantes
        {"flight": "TU998"},                                   # pas de features
        "n'importe quoi",
    ]
    assert n.handle(req) == 1
    assert sent[0]["payload"]["flight"] == "TU701"
    assert [e for e, *_ in logs].count("BAD_REQUEST_ITEM") == 3


def test_empty_or_malformed_payload_is_harmless(node):
    n, sent, _ = node
    assert n.handle({"type": "RISK_REQUEST", "corr_id": "c", "payload": {}}) == 0
    assert n.handle({"type": "RISK_REQUEST", "corr_id": "c"}) == 0
    assert not sent


def test_deadline_miss_is_logged(node):
    n, _, logs = node
    n.handle(make_request(ts_ns=time.time_ns() - 500_000_000))  # événement vieux de 500 ms
    assert any(e == "DEADLINE_MISS" for e, *_ in logs)


def test_no_deadline_miss_when_fast(node):
    n, _, logs = node
    n.handle(make_request())
    assert not any(e == "DEADLINE_MISS" for e, *_ in logs)


def test_run_loop_processes_queue_and_survives_bad_message(node):
    n, sent, _ = node
    inbox, stop = queue.Queue(), threading.Event()
    t = threading.Thread(target=n.run, args=(inbox, stop, 0.02))
    t.start()
    inbox.put({"type": "RISK_REQUEST", "payload": {"flights": 42}})  # défectueux
    inbox.put(make_request(flights=["TU701"]))
    deadline = time.time() + 3
    while not sent and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(timeout=2)
    assert len(sent) == 1 and not t.is_alive()


def test_next_version():
    assert next_version("risk-v1") == "risk-v2"
    assert next_version("risk-v9") == "risk-v10"
    assert next_version("custom") == "custom-v2"


def test_cloud_retrain_publishes_v2_and_node_hot_reloads(model_dir):
    sent, logs = [], []
    n = AINode(RiskPredictor(model_dir), sent.append, lambda e, d, c: logs.append((e, d, c)))
    n.handle(make_request(flights=["TU701"]))
    assert sent[-1]["payload"]["model_version"] == "risk-v1"

    meta = retrain_once(model_dir, n_samples=4000)
    assert meta and meta["version"] == "risk-v2"

    n.handle(make_request(flights=["TU701"]))           # rechargement entre deux requêtes
    assert sent[-1]["payload"]["model_version"] == "risk-v2"
    assert ("MODEL_RELOADED", "version risk-v2", "evt-0001") in logs


def test_cloud_retrain_rejects_degraded_model(model_dir, monkeypatch):
    import ai.retrain_cloud as rc

    monkeypatch.setattr(rc, "MIN_RECALL", 1.01)  # impossible à atteindre
    assert rc.retrain_once(model_dir, n_samples=3000) is None
    assert RiskPredictor(model_dir).model_version == "risk-v1"  # ancien modèle intact
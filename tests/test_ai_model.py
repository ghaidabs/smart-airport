import json
import threading
import time

import numpy as np
import pytest

from ai.generate_data import FEATURES, TARGET, generate
from ai.predictor import DEMO_FLIGHTS, FALLBACK_VERSION, RiskPredictor, rule_based_risk
from ai.train import publish_model, train


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    csv = d / "data.csv"
    generate(6000, seed=42).to_csv(csv, index=False)
    train(csv, d, report_dir=None, version="risk-test")
    return d


def test_generator_is_deterministic_and_in_ranges():
    a, b = generate(500, seed=1), generate(500, seed=1)
    assert a.equals(b)
    assert a["delay_min"].between(0, 90).all()
    assert a["slack_min"].between(35, 120).all()
    assert a["gate_distance_m"].between(100, 1500).all()
    assert a["security_wait_min"].between(0, 40).all()
    assert a["n_connecting"].between(1, 60).all()
    assert a["pax_total"].between(80, 300).all()
    assert a["hour"].between(6, 23).all()
    assert set(a["gate_changed"]) <= {0, 1}
    assert set(a[TARGET]) <= {0, 1}


def test_both_classes_present():
    assert 0.05 < generate(5000)[TARGET].mean() < 0.5


def test_meta_contents(model_dir):
    meta = json.loads((model_dir / "model_meta.json").read_text())
    assert meta["version"] == "risk-test" and meta["features"] == FEATURES
    assert meta["candidates"][meta["model_type"]]["test"]["recall"] > 0.8


def test_contract_keys_and_tu701_is_high_risk(model_dir):
    res = RiskPredictor(model_dir).predict(DEMO_FLIGHTS["TU701"])
    assert set(res) == {"risk", "model_version", "inference_ms"}
    assert res["model_version"] == "risk-test" and res["inference_ms"] > 0
    assert res["risk"] >= 0.7


def test_monotonic_sense(model_dir):
    p = RiskPredictor(model_dir)
    base = DEMO_FLIGHTS["TU701"]
    assert p.predict(dict(base, delay_min=35))["risk"] > p.predict(base)["risk"]
    assert p.predict(dict(base, gate_distance_m=150, security_wait_min=2))["risk"] < p.predict(base)["risk"]


def test_batch_matches_single_and_order(model_dir):
    p = RiskPredictor(model_dir)
    flights = list(DEMO_FLIGHTS.values())
    batch = [r["risk"] for r in p.predict_batch(flights)]
    single = [p.predict(f)["risk"] for f in flights]
    assert batch == pytest.approx(single)
    assert batch[0] > batch[1] > batch[2]  # TU701 > TU703 > TU702


def test_missing_or_invalid_features(model_dir):
    p = RiskPredictor(model_dir)
    with pytest.raises(ValueError):
        p.predict({"delay_min": 5})
    with pytest.raises(ValueError):
        p.predict(dict(DEMO_FLIGHTS["TU701"], delay_min=float("nan")))
    assert p.predict_batch([]) == []


def test_fallback_when_model_fails(model_dir):
    p = RiskPredictor(model_dir)

    class Broken:
        def predict_proba(self, X):
            raise RuntimeError("boom")

    p._state.model = Broken()
    res = p.predict(DEMO_FLIGHTS["TU701"])
    assert res["model_version"] == FALLBACK_VERSION
    assert res["risk"] == pytest.approx(rule_based_risk(DEMO_FLIGHTS["TU701"]))
    assert res["risk"] >= 0.7


def test_missing_model_fails_fast(tmp_path):
    with pytest.raises(FileNotFoundError):
        RiskPredictor(tmp_path)


def test_reload_picks_new_version_under_concurrent_predictions(model_dir, tmp_path_factory):
    # Le modèle v2 est entraîné AVANT de lancer les threads (pas de contention CPU,
    staging = tmp_path_factory.mktemp("staging")
    csv = staging / "data2.csv"
    generate(3000, seed=7).to_csv(csv, index=False)
    train(csv, staging, report_dir=None, version="risk-test2", seed=7)

    p = RiskPredictor(model_dir)
    errors, stop = [], threading.Event()

    def worker():
        while not stop.is_set():
            try:
                r = p.predict(DEMO_FLIGHTS["TU701"])
                assert r["model_version"] in {"risk-test", "risk-test2"} and 0 <= r["risk"] <= 1
            except Exception as e:  
                errors.append(e)
                return
            time.sleep(0.0005)  # laisse respirer le thread principal

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    try:
        for t in threads:
            t.start()
        time.sleep(0.05)
        publish_model(staging, model_dir)
        assert p.reload() is True
        time.sleep(0.05)
    finally:  
        stop.set()
        for t in threads:
            t.join(timeout=2)
    assert not errors
    assert p.model_version == "risk-test2"
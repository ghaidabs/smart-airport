"""Tests de decision/policy.py (P2). Lancer : pytest tests/test_ai_policy.py -v"""
import pytest

from decision.policy import BUSES, CRITICAL, FAST_TRACK, NONE, NOTIFY, RESERVE_GATE, WATCH, decide


@pytest.mark.parametrize(
    "risk, level",
    [(0.0, NONE), (0.399, NONE), (0.4, WATCH), (0.65, WATCH), (0.699, WATCH), (0.7, CRITICAL), (1.0, CRITICAL)],
)
def test_thresholds(risk, level):
    assert decide("TU701", risk)["level"] == level


def test_critical_actions_without_bus_when_gate_is_close():
    d = decide("TU701", 0.91, {"gate_distance_m": 150})
    assert d["actions"] == [RESERVE_GATE, FAST_TRACK, NOTIFY]
    assert BUSES not in d["actions"]


def test_critical_adds_buses_beyond_600_m():
    assert BUSES in decide("TU701", 0.91, {"gate_distance_m": 601})["actions"]
    assert BUSES not in decide("TU701", 0.91, {"gate_distance_m": 600})["actions"]


def test_no_context_means_no_bus():
    assert BUSES not in decide("TU701", 0.9)["actions"]


def test_none_level_has_no_action():
    assert decide("TU702", 0.20)["actions"] == []


def test_priority_hint_is_the_risk():
    assert decide("TU703", 0.65)["priority_hint"] == pytest.approx(0.65)


@pytest.mark.parametrize("bad", [-0.1, 1.2, float("nan"), None, "0.5"])
def test_invalid_risk_rejected(bad):
    with pytest.raises(ValueError):
        decide("TU701", bad)
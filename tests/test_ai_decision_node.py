import pytest

from common import config
from common.protocol import Message, MessageType as T
from nodes.decision_node import DecisionNode

CORR = "evt-test-0001"


@pytest.fixture()
def node(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    n = DecisionNode(port=0)  # jamais démarré : aucun port ouvert
    n.setup()
    n.replies, n.broadcasts = [], []
    n.reply = lambda cause, t, dest, payload: n.replies.append((t, dest, payload))
    n.broadcast = lambda cause, t, payload: n.broadcasts.append((t, payload))
    yield n
    n.log.close()


def risk_msg(flight="TU701", risk=0.94):
    payload = {"flight": flight, "risk": risk, "model_version": "risk-v1", "inference_ms": 0.5,
               "features": {"n_connecting": 35}, "context": {"gate": "G12", "connection_gate": "G03"}}
    return Message(type=T.RISK_RESULT, source="ai", dest="decision", payload=payload, corr_id=CORR)


def gate_msg(gate, flight="TU701"):
    return Message(type=T.GATE_ASSIGNED, source="flight", dest="decision",
                   payload={"flight": flight, "gate": gate}, corr_id=CORR)


def test_critical_risk_requests_a_gate_near_the_connection(node):
    node.on_risk(risk_msg(risk=0.94))
    (typ, dest, payload), = node.replies
    assert (typ, dest) == (T.GATE_REQUEST, "flight")
    assert payload["flight"] == "TU701" and payload["near_gate"] == "G03"
    assert (CORR, "TU701") in node.pending and not node.broadcasts


@pytest.mark.parametrize(
    "risk, level, actions",
    [(0.5, "SURVEILLE", ["notify", "re_predict"]), (0.4, "SURVEILLE", ["notify", "re_predict"]),
     (0.1, "AUCUNE", []), (0.399, "AUCUNE", [])],
)
def test_non_critical_risk_is_decided_directly(node, risk, level, actions):
    node.on_risk(risk_msg("TU703", risk))
    assert not node.replies
    (typ, payload), = node.broadcasts
    assert typ == T.DECISION and payload["level"] == level and payload["actions"] == actions


def test_threshold_0_7_is_critical(node):
    node.on_risk(risk_msg(risk=0.7))
    assert node.replies and node.replies[0][0] == T.GATE_REQUEST


def test_far_gate_adds_bus_and_keeps_policy_actions(node):
    node.on_risk(risk_msg())
    node.on_gate_assigned(gate_msg("G12"))  # G12 est à 650 m de G03 (> 600)
    _, _, payload = node.replies[-1]
    assert payload["actions"] == ["reserve_gate", "bus", "fast_track", "notify"]
    assert payload["group_size"] == 35 and payload["gate"] == "G12"


def test_near_gate_has_no_bus(node, monkeypatch):
    monkeypatch.setitem(config.GATE_POSITIONS_M, "G15", 150)
    node.on_risk(risk_msg())
    node.on_gate_assigned(gate_msg("G15"))
    assert "bus" not in node.replies[-1][2]["actions"]


def test_no_gate_available(node):
    node.on_risk(risk_msg())
    node.on_gate_assigned(gate_msg(None))
    (_, payload), = node.broadcasts
    assert payload["level"] == "CRITIQUE" and payload["actions"] == ["no_gate", "notify"]


def test_unexpected_gate_assignment_is_ignored(node):
    node.on_gate_assigned(gate_msg("G15"))
    assert not node.replies and not node.broadcasts


def test_final_decision_after_passenger_done_carries_policy_actions(node):
    node.on_risk(risk_msg())
    node.on_gate_assigned(gate_msg("G12"))
    done = Message(type=T.PAX_DONE, source="passenger", dest="decision", corr_id=CORR,
                   payload={"flight": "TU701", "buses": 2, "eta_min": 4.2})
    node.on_pax_done(done)
    (_, payload), = node.broadcasts
    assert payload["level"] == "CRITIQUE" and payload["buses"] == 2
    assert payload["actions"] == ["reserve_gate", "bus", "fast_track", "notify"]
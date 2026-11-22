"""Nœud Décision, port 5003 (squelette P1).

Mock actuel (seuils de départ du guide) :

- ``RISK_RESULT`` : risque >= 0,7 -> CRITIQUE : ``GATE_REQUEST`` au nœud Vols ;
  0,4 à 0,7 -> SURVEILLE, < 0,4 -> AUCUNE : ``DECISION`` diffusée directement ;
- ``GATE_ASSIGNED`` : ``PAX_ACTION`` au nœud Passagers (bus si > 600 m) ;
- ``PAX_DONE`` : ``DECISION`` finale diffusée, avec ``latency_ms`` depuis ``t0_ns`` ;
- ``SECURITY_ALERT`` : ``DECISION`` CRITIQUE immédiate (bande 0).

À remplacer : ``decision.policy.decide`` (P2) et ``SmartDispatcher`` (P5).
"""

from __future__ import annotations

import time

from common import config
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode

GATE_DEADLINE_MS = 100  # D de T4


def latency_ms(msg: Message) -> float | None:
    t0 = msg.payload.get("t0_ns")
    return None if t0 is None else round((time.time_ns() - t0) / 1e6, 3)


class DecisionNode(BaseNode):
    name = "decision"

    def setup(self) -> None:
        #: (corr_id, vol) -> contexte de la décision en cours
        self.pending: dict[tuple[str, str], dict] = {}
        self.on(T.RISK_RESULT, self.on_risk)
        self.on(T.GATE_ASSIGNED, self.on_gate_assigned)
        self.on(T.PAX_DONE, self.on_pax_done)
        self.on(T.SECURITY_ALERT, self.on_security_alert)

    def decide(self, msg: Message, payload: dict) -> None:
        payload["latency_ms"] = latency_ms(msg)
        self.log.log("DECISION", msg.corr_id, payload)
        self.broadcast(msg, T.DECISION, payload)

    def on_risk(self, msg: Message) -> None:
        p = msg.payload
        flight, risk = p["flight"], p["risk"]
        if risk >= 0.7:
            ctx = p.get("context", {})
            self.pending[(msg.corr_id, flight)] = {
                "risk": risk, "n_connecting": p.get("features", {}).get("n_connecting", 0),
                "connection_gate": ctx.get("connection_gate", "G03")}
            self.reply(msg, T.GATE_REQUEST, "flight", {
                "flight": flight, "near_gate": ctx.get("connection_gate"),
                "deadline_ns": time.time_ns() + GATE_DEADLINE_MS * 1_000_000})
        elif risk >= 0.4:
            self.decide(msg, {"flight": flight, "level": "SURVEILLE", "risk": risk,
                              "actions": ["notify", "re_predict"]})
        else:
            self.decide(msg, {"flight": flight, "level": "AUCUNE", "risk": risk,
                              "actions": []})

    def on_gate_assigned(self, msg: Message) -> None:
        flight, gate = msg.payload["flight"], msg.payload["gate"]
        ctx = self.pending.get((msg.corr_id, flight))
        if ctx is None:
            self.log.log("UNEXPECTED", msg.corr_id, msg.short())
            return
        if gate is None:
            self.pending.pop((msg.corr_id, flight))
            self.decide(msg, {"flight": flight, "level": "CRITIQUE", "risk": ctx["risk"],
                              "actions": ["no_gate", "notify"]})
            return
        actions = ["fast_track", "notify"]
        if config.gate_distance(gate, ctx["connection_gate"]) > config.BUS_DISTANCE_THRESHOLD_M:
            actions.insert(0, "bus")
        ctx.update(gate=gate, actions=actions)
        self.reply(msg, T.PAX_ACTION, "passenger", {
            "flight": flight, "group_size": ctx["n_connecting"], "risk": ctx["risk"],
            "gate": gate, "connection_gate": ctx["connection_gate"], "actions": actions})

    def on_pax_done(self, msg: Message) -> None:
        flight = msg.payload["flight"]
        ctx = self.pending.pop((msg.corr_id, flight), None)
        if ctx is None:
            self.log.log("UNEXPECTED", msg.corr_id, msg.short())
            return
        self.decide(msg, {"flight": flight, "level": "CRITIQUE", "risk": ctx["risk"],
                          "gate": ctx["gate"], "actions": ctx["actions"],
                          "buses": msg.payload["buses"], "eta_min": msg.payload["eta_min"]})

    def on_security_alert(self, msg: Message) -> None:
        self.decide(msg, {"flight": None, "level": "CRITIQUE",
                          "actions": ["open_security_lane"], **msg.payload})


if __name__ == "__main__":
    DecisionNode().run()

from __future__ import annotations

import time

from common import config
from common.protocol import Message, MessageType as T
from decision.policy import CRITICAL, decide as policy_decide
from nodes.base_node import BaseNode

GATE_DEADLINE_MS = 100  


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
        policy = policy_decide(flight, risk)  # P2 : niveau et actions selon le risque
        if policy["level"] == CRITICAL:
            ctx = p.get("context", {})
            self.pending[(msg.corr_id, flight)] = {
                "risk": risk, "n_connecting": p.get("features", {}).get("n_connecting", 0),
                "connection_gate": ctx.get("connection_gate", "G03")}
            self.reply(msg, T.GATE_REQUEST, "flight", {
                "flight": flight, "near_gate": ctx.get("connection_gate"),
                "deadline_ns": time.time_ns() + GATE_DEADLINE_MS * 1_000_000})
        else:  # SURVEILLE ou AUCUNE
            self.decide(msg, {"flight": flight, "level": policy["level"], "risk": risk,
                              "actions": policy["actions"]})

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
        # La distance réelle de la porte attribuée décide du bus (règle de decision/policy.py).
        distance = config.gate_distance(gate, ctx["connection_gate"])
        actions = policy_decide(flight, ctx["risk"], {"gate_distance_m": distance})["actions"]
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
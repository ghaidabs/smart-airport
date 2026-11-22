"""Nœud Vols et Ressources, port 5001 (squelette P1).

Mock actuel :

- ``FLIGHT_DELAY`` : met à jour le retard ; au-delà de 5 min, envoie un
  ``RISK_REQUEST`` au nœud IA avec tous les vols suivis ;
- ``GATE_UNAVAILABLE`` : marque la porte indisponible ; le vol qui l'occupait perd sa
  porte (``gate_changed = 1``) et un ``RISK_REQUEST`` est envoyé ;
- ``GATE_REQUEST`` : garde la porte actuelle si elle est utilisable, sinon prend la
  première porte libre (sans tenir compte de ``near_gate``).

À remplacer : T1 FlightMonitor (P3) et ``GateManager.reserve`` (P4).
"""

from __future__ import annotations

import copy
import random

from common import config
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode

DELAY_THRESHOLD_MIN = 5
DOWN = "DOWN"


def synthetic_flight(name: str) -> dict:
    """Vol synthétique (TU8xx, charge x2), déterministe à partir de son nom."""
    rng = random.Random(f"{config.SEED}-{name}")
    return {"pax_total": rng.randint(80, 300), "n_connecting": rng.randint(1, 60),
            "slack_min": rng.randint(35, 120), "gate": None,
            "connection_gate": "G03", "hour": 10}


class FlightNode(BaseNode):
    name = "flight"

    def setup(self) -> None:
        self.flights: dict[str, dict] = copy.deepcopy(config.FLIGHTS)
        for f in self.flights.values():
            f.update(delay_min=0, gate_changed=0)
        #: porte -> vol qui l'occupe, None (libre) ou DOWN (indisponible)
        self.gates: dict[str, str | None] = {g: None for g in config.GATES}
        for name, f in self.flights.items():
            if f["gate"] in self.gates:
                self.gates[f["gate"]] = name
        self.security_wait_min = config.DEFAULT_SECURITY_WAIT_MIN
        self.on(T.FLIGHT_DELAY, self.on_delay)
        self.on(T.GATE_UNAVAILABLE, self.on_gate_unavailable)
        self.on(T.GATE_REQUEST, self.on_gate_request)

    # --- Événements ----------------------------------------------------

    def on_delay(self, msg: Message) -> None:
        name = msg.payload["flight"]
        if name not in self.flights:
            self.flights[name] = synthetic_flight(name) | {"delay_min": 0, "gate_changed": 0}
        self.flights[name]["delay_min"] = msg.payload["delay_min"]
        self.log.log("FLIGHT_STATE", msg.corr_id, {"flight": name, **self.flights[name]})
        if msg.payload["delay_min"] > DELAY_THRESHOLD_MIN:
            self.request_risk(msg)

    def on_gate_unavailable(self, msg: Message) -> None:
        gate = msg.payload["gate"]
        holder = self.gates.get(gate)
        self.gates[gate] = DOWN
        self.log.log("GATE_DOWN", msg.corr_id, {"gate": gate, "flight": holder})
        if holder not in (None, DOWN):
            self.flights[holder].update(gate=None, gate_changed=1)
            self.request_risk(msg)

    def on_gate_request(self, msg: Message) -> None:
        name = msg.payload["flight"]
        flight = self.flights.get(name)
        gate = None
        if flight is not None:
            gate = flight["gate"] if flight["gate"] in self.gates else None
            if gate is None:
                gate = next((g for g in config.GATES if self.gates[g] is None), None)
                if gate is not None:
                    self.gates[gate] = name
                    flight["gate"] = gate
        self.log.log("GATE_RESERVED", msg.corr_id, {"flight": name, "gate": gate})
        self.reply(msg, T.GATE_ASSIGNED, "decision", {"flight": name, "gate": gate})

    # --- Vers l'IA -----------------------------------------------------

    def features(self, f: dict) -> dict:
        """Variables d'entrée du modèle (annexe B)."""
        gate = f["gate"] or f["connection_gate"]
        return {
            "delay_min": f["delay_min"], "slack_min": f["slack_min"],
            "gate_distance_m": config.gate_distance(gate, f["connection_gate"]),
            "security_wait_min": self.security_wait_min,
            "n_connecting": f["n_connecting"], "pax_total": f["pax_total"],
            "hour": f["hour"], "gate_changed": f["gate_changed"],
        }

    def request_risk(self, cause: Message) -> None:
        flights = [{"flight": name, "features": self.features(f),
                    "context": {"gate": f["gate"], "connection_gate": f["connection_gate"]}}
                   for name, f in self.flights.items()]
        self.reply(cause, T.RISK_REQUEST, "ai", {"flights": flights})


if __name__ == "__main__":
    FlightNode().run()

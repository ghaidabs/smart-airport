"""Nœud Vols et Ressources, port 5001 (P1, T1 de P3).

Les handlers mettent à jour l'état, puis **T1 FlightMonitor** le lit toutes les
100 ms et envoie le ``RISK_REQUEST`` :

- ``FLIGHT_DELAY`` : met à jour le retard ;
- ``GATE_UNAVAILABLE`` : marque la porte indisponible, le vol qui l'occupait la perd ;
- ``QUEUE_UPDATE`` : met à jour l'attente sécurité, entrée du modèle IA ;
- ``GATE_REQUEST`` : garde la porte actuelle si elle est utilisable, sinon prend la
  première porte libre (mock, sans tenir compte de ``near_gate``).

Seul le thread principal touche ``self.gates``. À remplacer : ``GateManager.reserve`` (P4).
"""

from __future__ import annotations

import random
from dataclasses import asdict, replace

from common import config
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode
from rt.monitors import FlightMonitor, FlightRecord, FlightTable, reference_flights
from rt.runtime import net_message

DOWN = "DOWN"


def synthetic_flight(name: str) -> FlightRecord:
    """Vol synthétique (TU8xx, charge x2), déterministe à partir de son nom."""
    rng = random.Random(f"{config.SEED}-{name}")
    pax_total, n_connecting = rng.randint(80, 300), rng.randint(1, 60)
    return FlightRecord(name, None, None, rng.randint(35, 120), 0, n_connecting, pax_total, 10)


class FlightNode(BaseNode):
    name = "flight"

    def setup(self) -> None:
        self.table = FlightTable([
            replace(record, security_wait_min=config.DEFAULT_SECURITY_WAIT_MIN)
            for record in reference_flights()])
        #: porte -> vol qui l'occupe, None (libre) ou DOWN (indisponible)
        self.gates: dict[str, str | None] = {g: None for g in config.GATES}
        for record in self.table.snapshot():
            if record.gate in self.gates:
                self.gates[record.gate] = record.flight
        self.on(T.FLIGHT_DELAY, self.on_delay)
        self.on(T.GATE_UNAVAILABLE, self.on_gate_unavailable)
        self.on(T.QUEUE_UPDATE, self.on_queue_update)
        self.on(T.GATE_REQUEST, self.on_gate_request)
        self.rt.periodic("T1", FlightMonitor(self.table, self.send_risk_request, net_message))

    # --- Événements ----------------------------------------------------

    def on_delay(self, msg: Message) -> None:
        name = msg.payload["flight"]
        if self.table.get(name) is None:
            self.table.add(synthetic_flight(name))
        self.table.set_delay(name, msg.payload["delay_min"], msg.corr_id,
                             msg.payload.get("t0_ns"))
        self.log.log("FLIGHT_STATE", msg.corr_id, asdict(self.table.get(name)))

    def on_gate_unavailable(self, msg: Message) -> None:
        gate = msg.payload["gate"]
        holder = self.gates.get(gate)
        self.gates[gate] = DOWN
        self.log.log("GATE_DOWN", msg.corr_id, {"gate": gate, "flight": holder})
        if holder not in (None, DOWN):
            # La distance reste celle de l'ancienne porte jusqu'à la réaffectation.
            self.table.set_gate(holder, None, msg.corr_id, t0_ns=msg.payload.get("t0_ns"))

    def on_queue_update(self, msg: Message) -> None:
        """Attente de sécurité envoyée par le nœud Passagers : entrée du modèle IA."""
        wait = msg.payload["wait_min"]
        self.table.set_security_wait(wait, msg.corr_id, msg.payload.get("t0_ns"))
        self.log.log("SECURITY_WAIT", msg.corr_id, {"wait_min": wait})

    def on_gate_request(self, msg: Message) -> None:
        name = msg.payload["flight"]
        record = self.table.get(name)
        gate = None
        if record is not None:
            gate = record.gate if record.gate in self.gates else None
            if gate is None:
                gate = next((g for g in config.GATES if self.gates[g] is None), None)
                if gate is not None:
                    self.gates[gate] = name
                    self.table.assign_gate(name, gate,
                                           config.gate_distance(gate, record.connection_gate))
        self.log.log("GATE_RESERVED", msg.corr_id, {"flight": name, "gate": gate})
        self.reply(msg, T.GATE_ASSIGNED, "decision", {"flight": name, "gate": gate})

    # --- Vers l'IA (thread T1) -----------------------------------------

    def send_risk_request(self, dest: str, msg: Message) -> None:
        self.log.log("T1_DETECT", msg.corr_id, {
            "flights": len(msg.payload["flights"]),
            "merged_corr_ids": msg.payload.get("merged_corr_ids", [])})
        self.send(dest, msg)


if __name__ == "__main__":
    FlightNode().run()

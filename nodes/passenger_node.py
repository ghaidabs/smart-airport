"""Nœud Passagers, port 5004 (P1, T2 et T5 de P3).

- **T2 QueueMonitor** (500 ms) simule la file de sécurité, déclenche T5 au-delà de
  100 personnes et publie l'attente vers le nœud Vols quand elle varie d'au moins 2 min ;
- **T5 SecurityAlert** (sporadique, D = 50 ms) envoie ``SECURITY_ALERT`` au nœud Décision ;
- ``QUEUE_UPDATE`` fixe la file, publie l'attente et évalue l'alerte sans attendre T2 ;
- ``PAX_ACTION`` : bus de 25 places si l'action ``bus`` est demandée, temps de trajet
  vers la porte de correspondance, puis ``PAX_DONE``.

À remplacer : le calcul des bus par ``BusPool`` (P4).
"""

from __future__ import annotations

import math
import time

from common import config
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode
from rt.monitors import QueueMonitor, SecurityAlert
from rt.runtime import net_message

BUS_CAPACITY = 25
OPEN_LANES = 1


class PassengerNode(BaseNode):
    name = "passenger"

    def setup(self) -> None:
        self.t5_events = self.rt.sporadic("T5", SecurityAlert(self.send, net_message))
        self.queue = QueueMonitor(self.raise_alert, message_factory=net_message,
                                  lanes=OPEN_LANES, seed=config.SEED,
                                  on_wait_change=self.publish_wait)
        self.rt.periodic("T2", self.queue)
        self.on(T.QUEUE_UPDATE, self.on_queue)
        self.on(T.PAX_ACTION, self.on_pax_action)

    def raise_alert(self, alert: Message) -> None:
        """Active T5. Une alerte perdue est journalisée."""
        if not self.t5_events.put(alert, timeout=0):
            self.log.log("DROP", alert.corr_id, alert.short())

    def publish_wait(self, wait_min: float) -> None:
        """T2 : l'attente simulée a changé, nouvel événement pour les entrées de l'IA."""
        msg = Message(T.QUEUE_UPDATE, self.name, "flight",
                      {"queue_len": self.queue.queue_len, "wait_min": round(wait_min, 1),
                       "t0_ns": time.time_ns()})
        self.log.log("QUEUE_STATE", msg.corr_id, msg.payload)
        self.send("flight", msg)

    def on_queue(self, msg: Message) -> None:
        queue_len = msg.payload["queue_len"]
        self.queue.set_queue(queue_len, msg.corr_id, msg.payload.get("t0_ns"))
        wait = round(self.queue.wait_min(queue_len), 1)
        self.log.log("QUEUE_STATE", msg.corr_id, {"queue_len": queue_len, "wait_min": wait})
        # L'attente de sécurité est une entrée du modèle IA : le nœud Vols doit la connaître.
        self.reply(msg, T.QUEUE_UPDATE, "flight", {"queue_len": queue_len, "wait_min": wait})
        self.queue.check_alert()

    def on_pax_action(self, msg: Message) -> None:
        p = msg.payload
        dist = config.gate_distance(p["gate"], p.get("connection_gate", "G03"))
        if "bus" in p["actions"]:
            buses = min(config.N_BUSES, math.ceil(p["group_size"] / BUS_CAPACITY))
            eta = dist / config.BUS_SPEED_M_PER_MIN + 2  # + embarquement dans le bus
        else:
            buses = 0
            eta = dist / config.WALK_SPEED_M_PER_MIN
        self.reply(msg, T.PAX_DONE, "decision",
                   {"flight": p["flight"], "buses": buses, "eta_min": round(eta, 1)})


if __name__ == "__main__":
    PassengerNode().run()

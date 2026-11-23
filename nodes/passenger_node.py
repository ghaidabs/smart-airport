"""Nœud Passagers, port 5004 (squelette P1).

Mock actuel :

- ``QUEUE_UPDATE`` : attente = file / débit (10 pax/min par file ouverte) ; au-delà de
  100 personnes, ``SECURITY_ALERT`` au nœud Décision ;
- ``PAX_ACTION`` : bus (25 places) si l'action ``bus`` est demandée, temps de trajet
  vers la porte de correspondance, puis ``PAX_DONE``.

À remplacer : T2 QueueMonitor et T5 SecurityAlert (P3), ``BusPool`` (P4).
"""

from __future__ import annotations

import math

from common import config
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode

BUS_CAPACITY = 25
OPEN_LANES = 1


class PassengerNode(BaseNode):
    name = "passenger"

    def setup(self) -> None:
        self.queue_len = 0
        self.on(T.QUEUE_UPDATE, self.on_queue)
        self.on(T.PAX_ACTION, self.on_pax_action)

    def on_queue(self, msg: Message) -> None:
        self.queue_len = msg.payload["queue_len"]
        wait = round(self.queue_len / (config.SECURITY_RATE_PAX_PER_MIN * OPEN_LANES), 1)
        self.log.log("QUEUE_STATE", msg.corr_id, {"queue_len": self.queue_len, "wait_min": wait})
        # L'attente de sécurité est une entrée du modèle IA : le nœud Vols doit la connaître.
        self.reply(msg, T.QUEUE_UPDATE, "flight", {"queue_len": self.queue_len, "wait_min": wait})
        if self.queue_len > config.SECURITY_ALERT_THRESHOLD:
            self.reply(msg, T.SECURITY_ALERT, "decision",
                       {"queue_len": self.queue_len, "wait_min": wait})

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
"""Nœud IA, port 5002 (squelette P1).

Mock actuel : ``RISK_REQUEST`` -> un ``RISK_RESULT`` par vol. Les vols du scénario
reçoivent les risques du guide (TU701 = 0,87, TU703 = 0,65, TU702 = 0,20) pour que le
scénario soit reproductible ; les autres un risque aléatoire (graine 42).

À remplacer par P2 : ``RiskPredictor.predict(features)`` (tâche T3).
Le champ ``context`` du vol est renvoyé tel quel pour le nœud Décision.
"""

from __future__ import annotations

import random
import time

from common import config
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode

SCENARIO_RISKS = {"TU701": 0.87, "TU703": 0.65, "TU702": 0.20}


class AINode(BaseNode):
    name = "ai"

    def setup(self) -> None:
        self.rng = random.Random(config.SEED)
        self.on(T.RISK_REQUEST, self.on_risk_request)

    def predict(self, flight: str, features: dict) -> dict:
        start = time.perf_counter_ns()
        risk = SCENARIO_RISKS.get(flight)
        if risk is None:
            risk = round(self.rng.random(), 2)
        return {"risk": risk, "model_version": "mock-0",
                "inference_ms": (time.perf_counter_ns() - start) / 1e6}

    def on_risk_request(self, msg: Message) -> None:
        for item in msg.payload["flights"]:
            result = self.predict(item["flight"], item["features"])
            self.reply(msg, T.RISK_RESULT, "decision",
                       {"flight": item["flight"], **result,
                        "features": item["features"],
                        "context": item.get("context", {})})


if __name__ == "__main__":
    AINode().run()

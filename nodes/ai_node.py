from __future__ import annotations

from ai.node_logic import AINode
from ai.predictor import RiskPredictor
from common.protocol import Message, MessageType as T
from nodes.base_node import BaseNode


class AINodeService(BaseNode):
    name = "ai"

    def setup(self) -> None:
        self.predictor = RiskPredictor()  # échoue vite si le modèle est absent
        self.logic = AINode(self.predictor, log=lambda event, detail, corr_id: self.log.log(event, corr_id, detail))
        self.t3_events = self.rt.sporadic("T3", self.run_inference)
        self.on(T.RISK_REQUEST, self.on_risk_request)
        self.log.log("MODEL_LOADED", detail=f"version {self.predictor.model_version}")
        print(f"[ai] modèle {self.predictor.model_version} chargé", flush=True)

    def on_risk_request(self, msg: Message) -> None:
        """Active T3. Le thread réseau et la boucle du nœud restent disponibles."""
        if not self.t3_events.put(msg, timeout=0):
            self.log.log("DROP", msg.corr_id, msg.short())

    def run_inference(self, msg: Message) -> None:
        """Corps de T3."""
        # reply() recopie corr_id et t0_ns : la latence de bout en bout reste calculable.
        self.logic.handle(
            msg.to_dict(),
            send=lambda m: self.reply(msg, m["type"], m["dest"], m["payload"]),
        )


if __name__ == "__main__":
    AINodeService().run()

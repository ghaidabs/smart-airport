"""Nœud IA (port 5002) : reçoit RISK_REQUEST, renvoie RISK_RESULT (tâche T3).

Conception : la LOGIQUE (``AINode``) ne dépend que de dictionnaires au format de
l'annexe A et de deux fonctions injectées (``send`` et ``log``). Elle ne connaît
ni les sockets ni les classes de P1/P3/P4 :
    * testable seule (tests/test_ai_node.py), avec une file et une liste ;
    * branchable sur ``common.net`` (P1), ``rt.SporadicTask`` (P3) et
      ``sync.EventBuffer`` (P4) sans modifier la logique.

Choix d'interface (à valider avec P1, voir README du dossier) :
    * un RISK_RESULT PAR VOL (payload : flight, risk, model_version, inference_ms),
      conforme à l'annexe A ; les résultats sont envoyés du risque le plus élevé
      au plus faible, pour que TU701 atteigne le nœud Décision en premier ;
    * le corr_id de la requête est recopié dans chaque résultat ;
    * une re-prédiction est une simple nouvelle RISK_REQUEST (même corr_id).
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from ai.generate_data import FEATURES
from ai.predictor import RiskPredictor

NODE_NAME = "ai"
DEFAULT_PORT = 5002
T3_DEADLINE_MS = 200.0  # table des tâches v0 : D de T3

SendFn = Callable[[dict], None]
LogFn = Callable[[str, str, str], None]  # (event, detail, corr_id)


def _default_log(event: str, detail: str, corr_id: str) -> None:
    logging.getLogger("node.ai").info("%s %s %s", corr_id, event, detail)


def _valid_features(features: Any) -> bool:
    """Toutes les variables présentes et numériques finies (sinon le vol est ignoré)."""
    if not isinstance(features, Mapping):
        return False
    try:
        return all(math.isfinite(float(features[name])) for name in FEATURES)
    except (KeyError, TypeError, ValueError):
        return False


class AINode:
    """Traite les requêtes de risque, une à la fois (T3 est sporadique)."""

    def __init__(
        self,
        predictor: RiskPredictor,
        send: SendFn,
        log: LogFn | None = None,
        deadline_ms: float = T3_DEADLINE_MS,
    ) -> None:
        self.predictor = predictor
        self._send = send
        self._log = log or _default_log
        self.deadline_ms = deadline_ms

    # ------------------------------------------------------------------ traitement
    def handle(self, msg: Mapping[str, Any]) -> int:
        """Traite un message ; renvoie le nombre de RISK_RESULT envoyés."""
        corr_id = str(msg.get("corr_id", ""))
        if msg.get("type") != "RISK_REQUEST":
            self._log("IGNORED", f"type inattendu : {msg.get('type')}", corr_id)
            return 0

        # Entre deux requêtes seulement : jamais de rechargement pendant une inférence.
        if self.predictor.reload_if_changed():
            self._log("MODEL_RELOADED", f"version {self.predictor.model_version}", corr_id)

        requested = (msg.get("payload") or {}).get("flights") or []
        flights: list[tuple[str, Mapping[str, float]]] = []
        for item in requested:
            name = item.get("flight") if isinstance(item, Mapping) else None
            feats = item.get("features") if isinstance(item, Mapping) else None
            if name and _valid_features(feats):
                flights.append((str(name), feats))
            else:
                self._log("BAD_REQUEST_ITEM", f"vol ignoré : {item!r}", corr_id)
        if not flights:
            return 0

        results = self.predictor.predict_batch([f for _, f in flights])  # UN appel pour tout le lot
        ranked = sorted(zip(flights, results), key=lambda fr: fr[1]["risk"], reverse=True)

        for (name, _), res in ranked:
            self._send(
                {
                    "id": str(uuid.uuid4()),
                    "type": "RISK_RESULT",
                    "source": NODE_NAME,
                    "dest": "decision",
                    "ts_ns": time.time_ns(),
                    "corr_id": corr_id,
                    "payload": {
                        "flight": name,
                        "risk": res["risk"],
                        "model_version": res["model_version"],
                        "inference_ms": res["inference_ms"],
                    },
                }
            )

        summary = " ".join(f"{n}={r['risk']:.2f}" for (n, _), r in ranked)
        self._log("RISK_RESULT", f"{summary} (inf. {ranked[0][1]['inference_ms']:.1f} ms)", corr_id)

        # La deadline de T3 se compte depuis l'horodatage de l'événement.
        ts_ns = msg.get("ts_ns")
        if isinstance(ts_ns, int):
            elapsed_ms = (time.time_ns() - ts_ns) / 1e6
            if elapsed_ms > self.deadline_ms:
                self._log("DEADLINE_MISS", f"T3 {elapsed_ms:.1f} ms > D={self.deadline_ms:.0f} ms", corr_id)
        return len(ranked)

    # ------------------------------------------------------------------ boucle
    def run(self, inbox: "queue.Queue[dict] | Any", stop: threading.Event, poll_s: float = 0.1) -> None:
        """Boucle de service : ``inbox.get(timeout)`` (queue.Queue ou EventBuffer de P4)."""
        while not stop.is_set():
            try:
                msg = inbox.get(timeout=poll_s)
            except queue.Empty:
                continue
            if msg is None:  # le EventBuffer de P4 peut renvoyer None à l'expiration
                continue
            try:
                self.handle(msg)
            except Exception:  # noqa: BLE001 - un message défectueux ne doit pas tuer le nœud
                logging.getLogger("node.ai").exception("échec de traitement du message")


def main() -> None:
    """Point d'entrée du nœud (lancé par run_all.sh). Nécessite le socle réseau de P1."""
    try:  # noqa: SIM105
        from common import config, net  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "common/net.py (P1) est introuvable : le nœud IA attend le socle réseau.\n"
            "Pour tester la logique seule : pytest tests/test_ai_node.py"
        ) from exc

    # --- Branchement P1 : à ajuster aux signatures réelles de common/net.py -------------
    # Hypothèses (annexe A et Guide P1) : NodeServer(port, inbox) dépose des dicts ou des
    # Message dans ``inbox`` ; send(dest, msg) envoie un message.
    predictor = RiskPredictor()
    inbox: queue.Queue = queue.Queue()  # remplacé par EventBuffer (P4) à l'intégration
    server = net.NodeServer(getattr(config, "AI_PORT", DEFAULT_PORT), inbox)
    node = AINode(predictor, send=lambda m: net.send(m["dest"], m))
    print(f"[ai     :{DEFAULT_PORT}] prêt · modèle {predictor.model_version} chargé")
    stop = threading.Event()
    server.start()
    try:
        node.run(inbox, stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
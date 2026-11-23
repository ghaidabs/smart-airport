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
        send: SendFn | None = None,
        log: LogFn | None = None,
        deadline_ms: float = T3_DEADLINE_MS,
    ) -> None:
        self.predictor = predictor
        self._send = send
        self._log = log or _default_log
        self.deadline_ms = deadline_ms

    # traitement
    def handle(self, msg: Mapping[str, Any], send: SendFn | None = None) -> int:
        """Traite un message ; renvoie le nombre de RISK_RESULT envoyés.

        ``send`` remplace, pour cet appel, la fonction d'envoi du constructeur.
        """
        emit = send or self._send
        if emit is None:
            raise ValueError("aucune fonction d'envoi fournie")
        corr_id = str(msg.get("corr_id", ""))
        if msg.get("type") != "RISK_REQUEST":
            self._log("IGNORED", f"type inattendu : {msg.get('type')}", corr_id)
            return 0

        # Entre deux requêtes seulement : jamais de rechargement pendant une inférence.
        if self.predictor.reload_if_changed():
            self._log("MODEL_RELOADED", f"version {self.predictor.model_version}", corr_id)

        requested = (msg.get("payload") or {}).get("flights") or []
        flights: list[tuple[str, Mapping[str, float], Mapping | None]] = []
        for item in requested:
            name = item.get("flight") if isinstance(item, Mapping) else None
            feats = item.get("features") if isinstance(item, Mapping) else None
            if name and _valid_features(feats):
                flights.append((str(name), feats, item.get("context")))
            else:
                self._log("BAD_REQUEST_ITEM", f"vol ignoré : {item!r}", corr_id)
        if not flights:
            return 0

        results = self.predictor.predict_batch([f for _, f, _ in flights])  # UN appel pour tout le lot
        ranked = sorted(zip(flights, results), key=lambda fr: fr[1]["risk"], reverse=True)

        for (name, feats, ctx), res in ranked:
            payload = {
                "flight": name,
                "risk": res["risk"],
                "model_version": res["model_version"],
                "inference_ms": res["inference_ms"],
                "features": dict(feats),
            }
            if ctx is not None:
                payload["context"] = dict(ctx)
            emit(
                {
                    "id": str(uuid.uuid4()),
                    "type": "RISK_RESULT",
                    "source": NODE_NAME,
                    "dest": "decision",
                    "ts_ns": time.time_ns(),
                    "corr_id": corr_id,
                    "payload": payload,
                }
            )

        summary = " ".join(f"{n}={r['risk']:.2f}" for (n, _, _), r in ranked)
        self._log("RISK_RESULT", f"{summary} (inf. {ranked[0][1]['inference_ms']:.1f} ms)", corr_id)

        # La deadline de T3 se compte depuis l'horodatage de l'événement.
        ts_ns = msg.get("ts_ns")
        if isinstance(ts_ns, int):
            elapsed_ms = (time.time_ns() - ts_ns) / 1e6
            if elapsed_ms > self.deadline_ms:
                self._log("DEADLINE_MISS", f"T3 {elapsed_ms:.1f} ms > D={self.deadline_ms:.0f} ms", corr_id)
        return len(ranked)

    #boucle
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
            except Exception: 
                logging.getLogger("node.ai").exception("échec de traitement du message")
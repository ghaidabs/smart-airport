"""Corps des tâches T1 FlightMonitor, T2 QueueMonitor et T5 SecurityAlert.

Ces classes ne dépendent pas du réseau : elles reçoivent une fonction
``send(dest, msg)`` et, via ``message_factory``, le format du message. Les nœuds
leur passent ``BaseNode.send`` et ``rt.runtime.net_message``.
"""

from __future__ import annotations

import math
import random
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable

from common import config

SendFn = Callable[[str, Any], Any]
MessageFactory = Callable[[str, str, str, str, dict], Any]

DELAY_THRESHOLD_MIN = 5.0
QUEUE_ALERT_THRESHOLD = config.SECURITY_ALERT_THRESHOLD
SERVICE_PER_LANE_PER_MIN = config.SECURITY_RATE_PAX_PER_MIN
SIM_MIN_PER_T2_TICK = 0.5  # 1 s réelle = 1 min simulée, et T2 tourne toutes les 500 ms.
WAIT_PUBLISH_STEP_MIN = 2.0  # Évite une nouvelle prédiction à chaque petite variation.


def make_message(msg_type: str, source: str, dest: str, corr_id: str, payload: dict) -> dict:
    """Construit un message au format de l'annexe A."""
    return {"id": str(uuid.uuid4()), "type": msg_type, "source": source, "dest": dest,
            "ts_ns": time.time_ns(), "corr_id": corr_id, "payload": payload}


@dataclass(frozen=True)
class FlightRecord:
    """État d'un vol et variables d'entrée du modèle de risque (P2).

    ``gate`` vaut ``None`` quand le vol a perdu sa porte. ``gate_distance_m`` garde
    alors la dernière distance connue, car le modèle a besoin d'une valeur.
    """

    flight: str
    planned_gate: str | None
    gate: str | None
    slack_min: float
    gate_distance_m: float
    n_connecting: int
    pax_total: int
    hour: int
    delay_min: float = 0.0
    security_wait_min: float = 0.0
    corr_id: str = ""
    connection_gate: str = "G03"

    def features(self) -> dict[str, float | int]:
        """Renvoie les 8 variables du contrat ``RiskPredictor.predict``."""
        return {
            "delay_min": self.delay_min,
            "slack_min": self.slack_min,
            "gate_distance_m": self.gate_distance_m,
            "security_wait_min": self.security_wait_min,
            "n_connecting": self.n_connecting,
            "pax_total": self.pax_total,
            "hour": self.hour,
            "gate_changed": int(self.gate != self.planned_gate),
        }

    def context(self) -> dict[str, str | None]:
        """Informations hors modèle, recopiées par l'IA pour le nœud Décision."""
        return {"gate": self.gate, "connection_gate": self.connection_gate}


@dataclass(frozen=True)
class FlightChange:
    """Événement appliqué à la table, en attente de lecture par T1."""

    kind: str  # "delay", "gate" ou "security"
    corr_id: str
    t0_ns: int | None
    flight: str | None = None
    value: float | None = None


def reference_flights() -> list[FlightRecord]:
    """Vols du scénario de référence, depuis ``common.config`` (source unique)."""
    return [
        FlightRecord(name, f["gate"], f["gate"], f["slack_min"],
                     config.gate_distance(f["gate"], f["connection_gate"]),
                     f["n_connecting"], f["pax_total"], f["hour"],
                     connection_gate=f["connection_gate"])
        for name, f in config.FLIGHTS.items()
    ]


class FlightTable:
    """État partagé des vols, protégé par un verrou à section critique courte.

    Les handlers écrivent, T1 lit. ``drain`` rend l'état et les changements en une
    seule opération, donc aucun événement n'est perdu entre deux réveils de T1.
    """

    def __init__(self, flights: list[FlightRecord]) -> None:
        self._lock = threading.Lock()
        self._flights = {record.flight: record for record in flights}
        self._changes: list[FlightChange] = []
        self._security_wait = flights[0].security_wait_min if flights else 0.0

    def add(self, record: FlightRecord) -> FlightRecord:
        """Ajoute un vol s'il est absent, avec l'attente sécurité courante."""
        with self._lock:
            if record.flight not in self._flights:
                self._flights[record.flight] = replace(
                    record, security_wait_min=self._security_wait)
            return self._flights[record.flight]

    def get(self, flight: str) -> FlightRecord | None:
        """Renvoie l'état d'un vol, ou ``None`` s'il est inconnu."""
        with self._lock:
            return self._flights.get(flight)

    def set_delay(self, flight: str, delay_min: float, corr_id: str,
                  t0_ns: int | None = None) -> None:
        """Applique un événement ``FLIGHT_DELAY``."""
        with self._lock:
            self._flights[flight] = replace(self._flights[flight], delay_min=delay_min,
                                            corr_id=corr_id)
            self._changes.append(FlightChange("delay", corr_id, t0_ns, flight, delay_min))

    def set_gate(self, flight: str, gate: str | None, corr_id: str,
                 distance_m: float | None = None, t0_ns: int | None = None) -> None:
        """Applique un changement de porte subi, que T1 doit détecter."""
        with self._lock:
            record = self._flights[flight]
            distance = record.gate_distance_m if distance_m is None else distance_m
            self._flights[flight] = replace(record, gate=gate, gate_distance_m=distance,
                                            corr_id=corr_id)
            if gate != record.gate:
                self._changes.append(FlightChange("gate", corr_id, t0_ns, flight))

    def assign_gate(self, flight: str, gate: str, distance_m: float) -> None:
        """Enregistre la porte choisie par le système, sans relancer de prédiction.

        Un vol synthétique n'avait aucune porte prévue : sa première porte devient
        sa porte prévue et ne compte donc pas comme un changement.
        """
        with self._lock:
            record = self._flights[flight]
            planned = gate if record.planned_gate is None else record.planned_gate
            self._flights[flight] = replace(record, gate=gate, planned_gate=planned,
                                            gate_distance_m=distance_m)

    def set_security_wait(self, wait_min: float, corr_id: str = "",
                          t0_ns: int | None = None) -> None:
        """Met à jour l'attente sécurité utilisée dans les variables du modèle."""
        with self._lock:
            if wait_min == self._security_wait:
                return
            self._security_wait = wait_min
            self._flights = {name: replace(record, security_wait_min=wait_min)
                             for name, record in self._flights.items()}
            self._changes.append(FlightChange("security", corr_id, t0_ns, value=wait_min))

    def snapshot(self) -> list[FlightRecord]:
        """Renvoie une copie cohérente. Les enregistrements sont immuables."""
        with self._lock:
            return list(self._flights.values())

    def drain(self) -> tuple[list[FlightRecord], list[FlightChange]]:
        """Renvoie l'état courant et retire les changements en attente."""
        with self._lock:
            changes, self._changes = self._changes, []
            return list(self._flights.values()), changes


class FlightMonitor:
    """Corps de T1 : détecte un retard > 5 min, un changement de porte ou d'attente.

    Un réveil envoie au plus un ``RISK_REQUEST``, avec tous les vols. Comme T1 a une
    période de 100 ms, T3 reçoit au plus une requête par intervalle minimal. Si
    plusieurs événements sont regroupés, ``corr_id`` et ``t0_ns`` sont ceux du plus
    ancien : la latence affichée est donc la plus défavorable.
    """

    def __init__(self, table: FlightTable, send: SendFn,
                 message_factory: MessageFactory = make_message,
                 delay_threshold_min: float = DELAY_THRESHOLD_MIN) -> None:
        self.table = table
        self.send = send
        self.message_factory = message_factory
        self.delay_threshold_min = delay_threshold_min
        self._last_delay: dict[str, float] = {}

    def _disturbed(self, record: FlightRecord) -> bool:
        return record.delay_min > self.delay_threshold_min or record.gate != record.planned_gate

    def __call__(self) -> None:
        """Envoie un ``RISK_REQUEST`` si un changement pertinent est survenu."""
        records, changes = self.table.drain()
        causes: list[FlightChange] = []
        for change in changes:
            if change.kind == "delay":
                last = self._last_delay.get(change.flight, 0.0)
                self._last_delay[change.flight] = change.value
                if change.value > self.delay_threshold_min and change.value != last:
                    causes.append(change)
            elif change.kind == "gate" or any(self._disturbed(r) for r in records):
                causes.append(change)
        if not causes:
            return
        payload: dict[str, Any] = {"flights": [
            {"flight": r.flight, "features": r.features(), "context": r.context()}
            for r in records]}
        stamps = [c.t0_ns for c in causes if c.t0_ns is not None]
        if stamps:
            payload["t0_ns"] = min(stamps)
        corr_ids = list(dict.fromkeys(c.corr_id for c in causes if c.corr_id))
        if len(corr_ids) > 1:
            payload["merged_corr_ids"] = corr_ids[1:]
        self.send("ai", self.message_factory("RISK_REQUEST", "flight", "ai",
                                             causes[0].corr_id, payload))


class QueueMonitor:
    """Corps de T2 : simule la file de sécurité et déclenche T5.

    L'alerte part au franchissement du seuil, puis de nouveau si la file
    grandit de ``realert_step`` personnes. Elle se réarme sous le seuil.
    ``check_alert`` permet de réagir à un ``QUEUE_UPDATE`` sans attendre le réveil
    de T2, car T5 a une deadline de 50 ms et T2 une période de 500 ms.
    """

    def __init__(self, raise_alert: Callable[[Any], Any], *,
                 message_factory: MessageFactory = make_message, lanes: int = 1,
                 arrivals_per_min: float = 8.0, threshold: int = QUEUE_ALERT_THRESHOLD,
                 realert_step: int = 20, initial_len: int = 0, seed: int = 42,
                 on_wait_change: Callable[[float], Any] | None = None,
                 wait_step_min: float = WAIT_PUBLISH_STEP_MIN) -> None:
        self.raise_alert = raise_alert
        self.message_factory = message_factory
        self.lanes = lanes
        self.arrivals_per_min = arrivals_per_min
        self.threshold = threshold
        self.realert_step = realert_step
        self.on_wait_change = on_wait_change
        self.wait_step_min = wait_step_min
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._queue_len = initial_len
        self._corr_id = ""
        self._t0_ns: int | None = None
        self._last_alert: int | None = None
        self._published_wait = self.wait_min(initial_len)

    @property
    def queue_len(self) -> int:
        """Longueur actuelle de la file."""
        with self._lock:
            return self._queue_len

    def wait_min(self, queue_len: int) -> float:
        """Attente estimée : file / (10 passagers/min par file ouverte)."""
        return queue_len / (SERVICE_PER_LANE_PER_MIN * self.lanes)

    def set_queue(self, queue_len: int, corr_id: str = "", t0_ns: int | None = None) -> None:
        """Applique un événement ``QUEUE_UPDATE``, déjà publié par l'appelant."""
        with self._lock:
            self._queue_len = max(0, int(queue_len))
            self._corr_id = corr_id
            self._t0_ns = t0_ns
            self._published_wait = self.wait_min(self._queue_len)

    def _poisson(self, mean: float) -> int:
        limit = math.exp(-mean)
        count, product = 0, self._rng.random()
        while product > limit:
            count += 1
            product *= self._rng.random()
        return count

    def _evaluate_locked(self) -> tuple[int, str, int | None] | None:
        """Décide l'alerte. L'événement courant n'est attribué qu'une seule fois."""
        observed = self._queue_len
        cause = (self._corr_id, self._t0_ns)
        self._corr_id, self._t0_ns = "", None
        if observed <= self.threshold:
            self._last_alert = None
            return None
        if self._last_alert is not None and observed < self._last_alert + self.realert_step:
            return None
        self._last_alert = observed
        return observed, cause[0], cause[1]

    def _emit(self, alert: tuple[int, str, int | None] | None) -> None:
        """Déclenche T5. Toujours appelée hors du verrou."""
        if alert is None:
            return
        queue_len, corr_id, t0_ns = alert
        payload: dict[str, Any] = {"queue_len": queue_len, "wait_min": self.wait_min(queue_len)}
        if t0_ns is not None:
            payload["t0_ns"] = t0_ns
        self.raise_alert(self.message_factory("SECURITY_ALERT", "passenger", "passenger",
                                              corr_id, payload))

    def check_alert(self) -> None:
        """Évalue la file tout de suite, sans simuler de pas de temps."""
        with self._lock:
            alert = self._evaluate_locked()
        self._emit(alert)

    def __call__(self) -> None:
        """Évalue la file observée, décide l'alerte, puis simule un pas de temps."""
        publish = None
        with self._lock:
            observed = self._queue_len
            alert = self._evaluate_locked()
            arrivals = self._poisson(self.arrivals_per_min * SIM_MIN_PER_T2_TICK)
            served = round(SERVICE_PER_LANE_PER_MIN * self.lanes * SIM_MIN_PER_T2_TICK)
            self._queue_len = max(0, observed + arrivals - served)
            wait = self.wait_min(self._queue_len)
            if abs(wait - self._published_wait) >= self.wait_step_min:
                self._published_wait = wait
                publish = wait
        self._emit(alert)  # Jamais d'envoi sous le verrou.
        if publish is not None and self.on_wait_change is not None:
            self.on_wait_change(publish)


class SecurityAlert:
    """Corps de T5 : transmet l'alerte de surcharge au nœud Décision."""

    def __init__(self, send: SendFn, message_factory: MessageFactory = make_message) -> None:
        self.send = send
        self.message_factory = message_factory

    def __call__(self, event: Any) -> None:
        """Envoie ``SECURITY_ALERT`` avec ``queue_len`` et ``wait_min``."""
        is_dict = isinstance(event, dict)
        payload = event["payload"] if is_dict else event.payload
        corr_id = event["corr_id"] if is_dict else event.corr_id
        body = {"queue_len": payload["queue_len"], "wait_min": payload["wait_min"]}
        if "t0_ns" in payload:  # Conserve la latence de bout en bout.
            body["t0_ns"] = payload["t0_ns"]
        self.send("decision", self.message_factory(
            "SECURITY_ALERT", "passenger", "decision", corr_id, body))

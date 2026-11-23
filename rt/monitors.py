"""Corps des tâches T1 FlightMonitor, T2 QueueMonitor et T5 SecurityAlert.

Ces classes ne dépendent pas du réseau : elles reçoivent une fonction
``send(dest, msg)``. P1 pourra donc les brancher dans ``nodes/`` en passant
``common.net.send`` et, via ``message_factory``, la classe ``Message``.
"""

from __future__ import annotations

import math
import random
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable

SendFn = Callable[[str, Any], Any]
MessageFactory = Callable[[str, str, str, str, dict], Any]

DELAY_THRESHOLD_MIN = 5.0
QUEUE_ALERT_THRESHOLD = 100
SERVICE_PER_LANE_PER_MIN = 10.0
SIM_MIN_PER_T2_TICK = 0.5  # 1 s réelle = 1 min simulée, et T2 tourne toutes les 500 ms.


def make_message(msg_type: str, source: str, dest: str, corr_id: str, payload: dict) -> dict:
    """Construit un message au format de l'annexe A."""
    return {"id": str(uuid.uuid4()), "type": msg_type, "source": source, "dest": dest,
            "ts_ns": time.time_ns(), "corr_id": corr_id, "payload": payload}


@dataclass(frozen=True)
class FlightRecord:
    """État d'un vol et variables d'entrée du modèle de risque (P2)."""

    flight: str
    planned_gate: str
    gate: str
    slack_min: float
    gate_distance_m: float
    n_connecting: int
    pax_total: int
    hour: int
    delay_min: float = 0.0
    security_wait_min: float = 0.0
    corr_id: str = ""

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


def reference_flights() -> list[FlightRecord]:
    """Vols du scénario de référence.

    TU701 suit l'énoncé (55 min de marge, 450 m). Les marges et distances de
    TU702 et TU703 sont illustratives : « confortable » et « moyenne ».
    """
    return [
        FlightRecord("TU701", "G12", "G12", 55, 450, 35, 240, 10),
        FlightRecord("TU702", "G18", "G18", 110, 300, 12, 180, 10),
        FlightRecord("TU703", "G20", "G20", 75, 600, 20, 200, 10),
    ]


class FlightTable:
    """État partagé des vols, protégé par un verrou à section critique courte."""

    def __init__(self, flights: list[FlightRecord]) -> None:
        self._lock = threading.Lock()
        self._flights = {record.flight: record for record in flights}

    def set_delay(self, flight: str, delay_min: float, corr_id: str) -> None:
        """Applique un événement ``FLIGHT_DELAY``."""
        with self._lock:
            self._flights[flight] = replace(self._flights[flight], delay_min=delay_min,
                                            corr_id=corr_id)

    def set_gate(self, flight: str, gate: str, corr_id: str,
                 distance_m: float | None = None) -> None:
        """Applique un changement de porte, avec sa nouvelle distance si connue."""
        with self._lock:
            record = self._flights[flight]
            distance = record.gate_distance_m if distance_m is None else distance_m
            self._flights[flight] = replace(record, gate=gate, gate_distance_m=distance,
                                            corr_id=corr_id)

    def set_security_wait(self, wait_min: float) -> None:
        """Met à jour l'attente sécurité utilisée dans les variables du modèle."""
        with self._lock:
            self._flights = {name: replace(record, security_wait_min=wait_min)
                             for name, record in self._flights.items()}

    def snapshot(self) -> list[FlightRecord]:
        """Renvoie une copie cohérente. Les enregistrements sont immuables."""
        with self._lock:
            return list(self._flights.values())


class FlightMonitor:
    """Corps de T1 : détecte retard > 5 min ou changement de porte."""

    def __init__(self, table: FlightTable, send: SendFn,
                 message_factory: MessageFactory = make_message,
                 delay_threshold_min: float = DELAY_THRESHOLD_MIN) -> None:
        self.table = table
        self.send = send
        self.message_factory = message_factory
        self.delay_threshold_min = delay_threshold_min
        self._seen: dict[str, tuple[float, str]] = {}

    def __call__(self) -> None:
        """Envoie un ``RISK_REQUEST`` par ``corr_id`` ayant des vols modifiés."""
        groups: dict[str, list[dict]] = {}
        for record in self.table.snapshot():
            last_delay, last_gate = self._seen.get(record.flight, (0.0, record.planned_gate))
            delay_event = (record.delay_min > self.delay_threshold_min
                           and record.delay_min != last_delay)
            if delay_event or record.gate != last_gate:
                groups.setdefault(record.corr_id, []).append(
                    {"flight": record.flight, "features": record.features()})
            self._seen[record.flight] = (record.delay_min, record.gate)
        for corr_id, flights in groups.items():
            self.send("ai", self.message_factory("RISK_REQUEST", "flight", "ai", corr_id,
                                                 {"flights": flights}))


class QueueMonitor:
    """Corps de T2 : simule la file de sécurité et déclenche T5.

    L'alerte part au franchissement du seuil, puis de nouveau si la file
    grandit de ``realert_step`` personnes. Elle se réarme sous le seuil.
    """

    def __init__(self, raise_alert: Callable[[Any], Any], *,
                 message_factory: MessageFactory = make_message, lanes: int = 1,
                 arrivals_per_min: float = 8.0, threshold: int = QUEUE_ALERT_THRESHOLD,
                 realert_step: int = 20, initial_len: int = 0, seed: int = 42) -> None:
        self.raise_alert = raise_alert
        self.message_factory = message_factory
        self.lanes = lanes
        self.arrivals_per_min = arrivals_per_min
        self.threshold = threshold
        self.realert_step = realert_step
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._queue_len = initial_len
        self._corr_id = ""
        self._last_alert: int | None = None

    @property
    def queue_len(self) -> int:
        """Longueur actuelle de la file."""
        with self._lock:
            return self._queue_len

    def wait_min(self, queue_len: int) -> float:
        """Attente estimée : file / (10 passagers/min par file ouverte)."""
        return queue_len / (SERVICE_PER_LANE_PER_MIN * self.lanes)

    def set_queue(self, queue_len: int, corr_id: str = "") -> None:
        """Applique un événement ``QUEUE_UPDATE``."""
        with self._lock:
            self._queue_len = max(0, int(queue_len))
            self._corr_id = corr_id

    def _poisson(self, mean: float) -> int:
        limit = math.exp(-mean)
        count, product = 0, self._rng.random()
        while product > limit:
            count += 1
            product *= self._rng.random()
        return count

    def __call__(self) -> None:
        """Évalue la file observée, décide l'alerte, puis simule un pas de temps."""
        alert = None
        with self._lock:
            observed = self._queue_len
            if observed > self.threshold:
                if self._last_alert is None or observed >= self._last_alert + self.realert_step:
                    self._last_alert = observed
                    alert = (observed, self._corr_id)
            else:
                self._last_alert = None
            arrivals = self._poisson(self.arrivals_per_min * SIM_MIN_PER_T2_TICK)
            served = round(SERVICE_PER_LANE_PER_MIN * self.lanes * SIM_MIN_PER_T2_TICK)
            self._queue_len = max(0, observed + arrivals - served)
        if alert is not None:  # Jamais d'envoi sous le verrou.
            queue_len, corr_id = alert
            self.raise_alert(self.message_factory(
                "SECURITY_ALERT", "passenger", "passenger", corr_id,
                {"queue_len": queue_len, "wait_min": self.wait_min(queue_len)}))


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
        self.send("decision", self.message_factory(
            "SECURITY_ALERT", "passenger", "decision", corr_id,
            {"queue_len": payload["queue_len"], "wait_min": payload["wait_min"]}))

"""Tâche sporadique réveillée par un tampon d'événements.

Deux activations sont séparées d'au moins l'intervalle minimal. Un événement
trop rapproché reste en file et attend. Son temps de réponse et sa deadline
partent toujours de l'horodatage de l'événement, donc cette attente est
visible dans les mesures.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Protocol

from rt.metrics import NS_PER_MS, JobRecord, MetricsCollector

LOGGER = logging.getLogger(__name__)


class EventSource(Protocol):
    """Interface de ``EventBuffer`` (P4, annexe B) utilisée par la tâche."""

    def get(self, timeout: float | None = None) -> Any:
        """Renvoie un événement, ou ``None`` après ``timeout`` secondes."""


class QueueEventSource:
    """Tampon borné de remplacement, en attendant ``sync.EventBuffer`` (P4)."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize)

    def put(self, msg: Any, timeout: float | None = None) -> bool:
        """Dépose ``msg``. Renvoie False si le tampon reste plein."""
        try:
            self._queue.put(msg, timeout=timeout)
        except queue.Full:
            return False
        return True

    def get(self, timeout: float | None = None) -> Any:
        """Retire un événement, ou renvoie ``None`` après ``timeout``."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None


def _field(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


class SporadicTask(threading.Thread):
    """Traite les événements de ``source`` en respectant l'intervalle minimal."""

    def __init__(
        self,
        name: str,
        min_interarrival_ms: float,
        deadline_ms: float,
        body: Callable[[Any], None],
        source: EventSource,
        *,
        metrics: MetricsCollector | None = None,
        poll_ms: float = 20.0,
    ) -> None:
        """Prépare la tâche. ``poll_ms`` borne le délai de prise en compte d'un arrêt."""
        if min_interarrival_ms <= 0 or deadline_ms <= 0:
            raise ValueError("min_interarrival_ms et deadline_ms doivent être positifs")
        super().__init__(name=name, daemon=True)
        self.min_interarrival_ns = round(min_interarrival_ms * NS_PER_MS)
        self.deadline_ns = round(deadline_ms * NS_PER_MS)
        self.body = body
        self.source = source
        self.metrics = metrics if metrics is not None else MetricsCollector()
        self.poll_s = poll_ms / 1000
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Demande l'arrêt. Le job en cours se termine normalement."""
        self._stop_event.set()

    @staticmethod
    def _arrival_ns(event: Any) -> int:
        """Convertit ``ts_ns`` (horloge murale) vers l'horloge ``perf_counter_ns``."""
        now = time.perf_counter_ns()
        stamp = _field(event, "ts_ns")
        if stamp is None:
            return now
        return now - max(0, time.time_ns() - int(stamp))

    def _wait_until(self, target_ns: int) -> bool:
        while True:
            remaining = target_ns - time.perf_counter_ns()
            if remaining <= 0:
                return not self._stop_event.is_set()
            if self._stop_event.wait(remaining / 1e9):
                return False

    def run(self) -> None:
        """Boucle : attente d'un événement, respect de l'intervalle, exécution."""
        last_start: int | None = None
        job = 0
        while not self._stop_event.is_set():
            event = self.source.get(timeout=self.poll_s)
            if event is None:
                continue
            arrival = self._arrival_ns(event)
            if last_start is not None and not self._wait_until(
                    last_start + self.min_interarrival_ns):
                break
            start = time.perf_counter_ns()
            cpu_start = time.thread_time_ns()
            error = False
            try:
                self.body(event)
            except Exception:  # Une erreur de job ne doit pas tuer la tâche.
                error = True
                LOGGER.exception("Erreur dans le job %d de %s", job, self.name)
            cpu = time.thread_time_ns() - cpu_start
            end = time.perf_counter_ns()
            last_start = start
            self.metrics.record(JobRecord(
                task=self.name, job=job, release_ns=arrival, start_ns=start,
                end_ns=end, deadline_ns=arrival + self.deadline_ns, cpu_ns=cpu,
                error=error, corr_id=str(_field(event, "corr_id") or "")))
            job += 1

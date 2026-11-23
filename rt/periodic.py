"""Tâche périodique à dates de réveil absolues.

Le réveil ``k`` est ``origine + k * P`` : une attente relative ``sleep(P)``
accumulerait le temps d'exécution et la latence de réveil, donc la période
dériverait. C'est l'équivalent Python de ``clock_gettime`` associé à
``pthread_cond_timedwait`` (Ch. 4).

Politique de dépassement par défaut : ``skip``. Si un job finit après le
réveil suivant, les réveils déjà passés sont sautés et comptés. Cela évite
l'accumulation de retard, et une surveillance n'a besoin que de l'état le plus
récent. ``catch_up`` exécute au contraire chaque réveil en retard.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from rt.metrics import NS_PER_MS, JobRecord, MetricsCollector
from rt.system import apply_task_priority

LOGGER = logging.getLogger(__name__)
OVERRUN_POLICIES = ("skip", "catch_up")


class PeriodicTask(threading.Thread):
    """Exécute ``body`` à chaque période et mesure chaque job."""

    def __init__(
        self,
        name: str,
        period_ms: float,
        deadline_ms: float,
        body: Callable[[], None],
        *,
        metrics: MetricsCollector | None = None,
        overrun_policy: str = "skip",
        offset_ms: float = 0.0,
        origin_ns: int | None = None,
        max_jobs: int | None = None,
    ) -> None:
        """Prépare la tâche.

        ``origin_ns`` (horloge ``perf_counter_ns``) aligne plusieurs tâches
        sur une même origine. Sinon, l'origine est la date de démarrage.
        """
        if period_ms <= 0 or deadline_ms <= 0:
            raise ValueError("period_ms et deadline_ms doivent être positifs")
        if overrun_policy not in OVERRUN_POLICIES:
            raise ValueError(f"politique inconnue : {overrun_policy}")
        super().__init__(name=name, daemon=True)
        self.period_ns = round(period_ms * NS_PER_MS)
        self.deadline_ns = round(deadline_ms * NS_PER_MS)
        self.body = body
        self.metrics = metrics if metrics is not None else MetricsCollector()
        self.overrun_policy = overrun_policy
        self.offset_ns = round(offset_ms * NS_PER_MS)
        self.origin_ns = origin_ns
        self.max_jobs = max_jobs
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Demande l'arrêt. Le job en cours se termine normalement."""
        self._stop_event.set()

    def _wait_until(self, target_ns: int) -> bool:
        """Attend la date absolue ``target_ns``. Renvoie False si arrêt demandé."""
        while True:
            remaining = target_ns - time.perf_counter_ns()
            if remaining <= 0:
                return not self._stop_event.is_set()
            if self._stop_event.wait(remaining / 1e9):
                return False

    def run(self) -> None:
        """Boucle périodique : attente absolue, exécution, mesure."""
        apply_task_priority(self.name)
        if self.origin_ns is None:
            self.origin_ns = time.perf_counter_ns()
        origin = self.origin_ns + self.offset_ns
        index = 0
        job = 0
        while self.max_jobs is None or job < self.max_jobs:
            release = origin + index * self.period_ns
            if not self._wait_until(release):
                break
            start = time.perf_counter_ns()
            cpu_start = time.thread_time_ns()
            error = False
            try:
                self.body()
            except Exception:  # Une erreur de job ne doit pas tuer la tâche.
                error = True
                LOGGER.exception("Erreur dans le job %d de %s", job, self.name)
            cpu = time.thread_time_ns() - cpu_start
            end = time.perf_counter_ns()

            index += 1
            skipped = 0
            if self.overrun_policy == "skip":
                late = end - (origin + index * self.period_ns)
                if late > 0:
                    skipped = (late - 1) // self.period_ns + 1
                    index += skipped
            self.metrics.record(JobRecord(
                task=self.name, job=job, release_ns=release, start_ns=start,
                end_ns=end, deadline_ns=release + self.deadline_ns, cpu_ns=cpu,
                skipped_after=skipped, error=error))
            job += 1

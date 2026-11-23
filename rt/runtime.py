"""Runtime temps réel d'un nœud : relie les tâches de ``rt`` au réseau de P1.

Chaque nœud possède un :class:`NodeRuntime`. Il crée ses tâches avec les paramètres
de la table officielle, collecte un ``JobRecord`` par job et écrit, à l'arrêt,
``logs/rt/<nœud>_jobs.csv``. Ces fichiers sont dans un sous-dossier car
``tools/timeline.py`` lit tous les ``logs/*.csv`` au format du journal.
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Callable

from common import config
from common.logger import CsvLogger
from common.protocol import Message
from rt.metrics import NS_PER_MS, JobRecord, MetricsCollector
from rt.periodic import PeriodicTask
from rt.sporadic import QueueEventSource, SporadicTask
from rt.system import apply_task_priority
from rt.tasks import TaskSpec, get_task, load_task_table

EVENT_BUFFER_SIZE = 256  # Remplacé par l'EventBuffer de P4 lors de son branchement.


def rt_log_dir() -> Path:
    """Dossier des mesures par job, lu au moment de l'appel (les tests le changent)."""
    return Path(config.LOG_DIR) / "rt"


def net_message(msg_type: str, source: str, dest: str, corr_id: str, payload: dict) -> Message:
    """Fabrique un ``Message`` de P1 pour les moniteurs.

    ``ts_ns`` vaut l'heure de l'événement initial si elle est connue : une tâche
    sporadique interne (T5) mesure ainsi sa deadline depuis l'événement. ``Sender``
    le remplace par l'heure d'envoi quand le message part sur le réseau.
    """
    msg = Message(msg_type, source, dest, payload, corr_id)
    msg.ts_ns = int(payload.get("t0_ns") or time.time_ns())
    return msg


class NodeRuntime:
    """Tâches et mesures temps réel d'un nœud."""

    def __init__(self, node: str, log: CsvLogger) -> None:
        self.node = node
        self.log = log
        self.metrics = MetricsCollector()
        self._specs = load_task_table()
        self._tasks: list[PeriodicTask | SporadicTask] = []
        self._started = False
        self._inline_jobs: dict[str, int] = {}

    def _spec(self, task_id: str) -> TaskSpec:
        return get_task(task_id, self._specs)

    def _guarded(self, task_id: str, body: Callable[..., None]) -> Callable[..., None]:
        """Journalise l'erreur d'un job. La tâche la compte ensuite et continue."""

        def run(*args: Any) -> None:
            try:
                body(*args)
            except Exception:
                corr_id = getattr(args[0], "corr_id", "") if args else ""
                self.log.log("HANDLER_ERROR", corr_id,
                             f"{task_id} {traceback.format_exc(limit=3)}")
                raise

        return run

    def periodic(self, task_id: str, body: Callable[[], None]) -> PeriodicTask:
        """Déclare une tâche périodique, démarrée par :meth:`start`."""
        spec = self._spec(task_id)
        task = PeriodicTask(task_id, spec.period_ms, spec.deadline_ms,
                            self._guarded(task_id, body), metrics=self.metrics)
        self._tasks.append(task)
        return task

    def sporadic(self, task_id: str, body: Callable[[Any], None]) -> QueueEventSource:
        """Déclare une tâche sporadique et renvoie son tampon d'événements."""
        spec = self._spec(task_id)
        source = QueueEventSource(EVENT_BUFFER_SIZE)
        self._tasks.append(SporadicTask(task_id, spec.period_ms, spec.deadline_ms,
                                        self._guarded(task_id, body), source,
                                        metrics=self.metrics))
        return source

    def run_job(self, task_id: str, msg: Message, body: Callable[[], None]) -> None:
        """Exécute et mesure un job dans le thread courant (T4 avant le dispatcher de P5).

        Aucun intervalle minimal n'est imposé : un même ``RISK_REQUEST`` produit
        plusieurs ``RISK_RESULT`` à traiter à la suite.
        """
        if task_id not in self._inline_jobs:
            apply_task_priority(task_id)
        spec = self._spec(task_id)
        now = time.perf_counter_ns()
        arrival = now - max(0, time.time_ns() - msg.ts_ns) if msg.ts_ns else now
        start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        error = False
        try:
            body()
        except Exception:
            error = True
            self.log.log("HANDLER_ERROR", msg.corr_id,
                         f"{task_id} {traceback.format_exc(limit=3)}")
        cpu = time.thread_time_ns() - cpu_start
        end = time.perf_counter_ns()
        job = self._inline_jobs.get(task_id, 0)
        self._inline_jobs[task_id] = job + 1
        self.metrics.record(JobRecord(
            task=task_id, job=job, release_ns=arrival,
            start_ns=start, end_ns=end,
            deadline_ns=arrival + round(spec.deadline_ms * NS_PER_MS),
            cpu_ns=cpu, error=error, corr_id=msg.corr_id))

    def start(self) -> None:
        """Démarre les tâches déclarées."""
        if self._started:
            return
        self._started = True
        for task in self._tasks:
            task.start()

    def stop(self, write: bool = True) -> None:
        """Arrête les tâches et écrit leurs mesures."""
        if self._started:
            for task in self._tasks:
                task.stop()
            for task in self._tasks:
                task.join(timeout=3)
            self._started = False
        if write and self.metrics.records():
            self.metrics.write_jobs_csv(rt_log_dir() / f"{self.node}_jobs.csv")

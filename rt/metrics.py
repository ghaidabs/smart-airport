"""Collecte des mesures par job, export CSV et résumé par tâche."""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

NS_PER_MS = 1_000_000

JOB_FIELDS = ("task", "job", "release_ns", "start_ns", "end_ns", "deadline_ns",
              "cpu_ns", "c_ms", "response_ms", "jitter_ms", "missed",
              "skipped_after", "error", "corr_id")
SUMMARY_FIELDS = ("task", "jobs", "misses", "skipped", "errors", "c_min_ms",
                  "c_mean_ms", "c_max_ms", "c_cpu_max_ms", "r_mean_ms",
                  "r_max_ms", "jitter_max_ms")


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Une exécution de tâche. Les dates viennent de ``perf_counter_ns``.

    ``release_ns`` est le réveil théorique d'un job périodique, ou l'arrivée
    de l'événement d'un job sporadique. ``cpu_ns`` exclut les préemptions,
    alors que ``c_ms`` (fin - début) les inclut.
    """

    task: str
    job: int
    release_ns: int
    start_ns: int
    end_ns: int
    deadline_ns: int
    cpu_ns: int = 0
    skipped_after: int = 0
    error: bool = False
    corr_id: str = ""

    @property
    def c_ms(self) -> float:
        """Durée écoulée entre le début et la fin du job."""
        return (self.end_ns - self.start_ns) / NS_PER_MS

    @property
    def response_ms(self) -> float:
        """Temps de réponse : fin - réveil."""
        return (self.end_ns - self.release_ns) / NS_PER_MS

    @property
    def jitter_ms(self) -> float:
        """Gigue de démarrage : début - réveil."""
        return (self.start_ns - self.release_ns) / NS_PER_MS

    @property
    def missed(self) -> bool:
        """Vrai si le job finit après sa deadline absolue."""
        return self.end_ns > self.deadline_ns


class MetricsCollector:
    """Accumule les ``JobRecord`` de plusieurs threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[JobRecord] = []

    def record(self, job: JobRecord) -> None:
        """Ajoute un job. La section critique ne fait aucune entrée-sortie."""
        with self._lock:
            self._records.append(job)

    def records(self, task: str | None = None) -> list[JobRecord]:
        """Renvoie une copie des jobs, éventuellement filtrée par tâche."""
        with self._lock:
            items = list(self._records)
        return [r for r in items if task is None or r.task == task]

    def summary(self) -> list[dict[str, float | int | str]]:
        """Calcule le résumé de chaque tâche, trié par nom."""
        return summarize(self.records())

    def write_jobs_csv(self, path: Path) -> None:
        """Écrit un job par ligne."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(JOB_FIELDS)
            for r in sorted(self.records(), key=lambda item: item.start_ns):
                writer.writerow((r.task, r.job, r.release_ns, r.start_ns, r.end_ns,
                                 r.deadline_ns, r.cpu_ns, f"{r.c_ms:.6f}",
                                 f"{r.response_ms:.6f}", f"{r.jitter_ms:.6f}",
                                 int(r.missed), r.skipped_after, int(r.error),
                                 r.corr_id))


def summarize(records: Iterable[JobRecord]) -> list[dict[str, float | int | str]]:
    """Résume des jobs : Cmin, Cmoy, Cmax, Rmax, dépassements, gigue max."""
    by_task: dict[str, list[JobRecord]] = {}
    for record in records:
        by_task.setdefault(record.task, []).append(record)

    rows: list[dict[str, float | int | str]] = []
    for task in sorted(by_task):
        jobs = by_task[task]
        costs = [j.c_ms for j in jobs]
        responses = [j.response_ms for j in jobs]
        rows.append({
            "task": task,
            "jobs": len(jobs),
            "misses": sum(j.missed for j in jobs),
            "skipped": sum(j.skipped_after for j in jobs),
            "errors": sum(j.error for j in jobs),
            "c_min_ms": min(costs),
            "c_mean_ms": sum(costs) / len(costs),
            "c_max_ms": max(costs),
            "c_cpu_max_ms": max(j.cpu_ns for j in jobs) / NS_PER_MS,
            "r_mean_ms": sum(responses) / len(responses),
            "r_max_ms": max(responses),
            "jitter_max_ms": max(j.jitter_ms for j in jobs),
        })
    return rows


def write_summary_csv(rows: Iterable[dict], path: Path, extra: dict | None = None) -> None:
    """Écrit un résumé, avec des colonnes constantes optionnelles (ex. la charge)."""
    extra = extra or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(*extra, *SUMMARY_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({**extra, **{k: _fmt(v) for k, v in row.items()}})


def format_summary(rows: Iterable[dict]) -> str:
    """Formate un résumé en tableau lisible dans la console."""
    head = (f"{'Tâche':<6}{'Jobs':>6}{'Dép.':>6}{'Saut.':>6}{'Cmin':>9}{'Cmoy':>9}"
            f"{'Cmax':>9}{'Rmax':>9}{'Gigue':>9}  (ms)")
    lines = [head]
    for r in rows:
        lines.append(f"{r['task']:<6}{r['jobs']:>6}{r['misses']:>6}{r['skipped']:>6}"
                     f"{r['c_min_ms']:>9.3f}{r['c_mean_ms']:>9.3f}{r['c_max_ms']:>9.3f}"
                     f"{r['r_max_ms']:>9.3f}{r['jitter_max_ms']:>9.3f}")
    return "\n".join(lines)


def _fmt(value: object) -> object:
    return f"{value:.6f}" if isinstance(value, float) else value

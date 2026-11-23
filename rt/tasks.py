"""Table officielle des tâches, source de vérité pour P5 et pour le rapport.

La table v0 vient de la section 4 du guide. Les C mesurés par
``experiments/m3_wcet.py`` sont stockés dans ``rt/measured_wcet.json`` et
remplacent les valeurs v0 au chargement.

Lancement : ``python -m rt.tasks``
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

MEASURED_WCET_PATH = Path(__file__).with_name("measured_wcet.json")


@dataclass(frozen=True)
class TaskSpec:
    """Paramètres temporels d'une tâche (durées en millisecondes).

    ``period_ms`` est la période d'une tâche périodique, ou l'intervalle
    minimal entre arrivées d'une tâche sporadique. Il vaut ``None`` pour une
    tâche apériodique. ``c_ms`` vaut ``None`` si la tâche n'a pas de C borné.
    """

    task_id: str
    name: str
    role: str
    node: str
    kind: str
    c_ms: float | None
    period_ms: float | None
    deadline_ms: float | None
    criticality: str
    c_source: str = "v0"

    @property
    def utilization(self) -> float:
        """Renvoie C/P, ou 0 pour une tâche hors de l'analyse."""
        if self.c_ms is None or self.period_ms is None:
            return 0.0
        return self.c_ms / self.period_ms


TASKS_V0: tuple[TaskSpec, ...] = (
    TaskSpec("T1", "FlightMonitor", "Détecter retards et changements de porte",
             "flight", "periodic", 5.0, 100.0, 100.0, "Haute"),
    TaskSpec("T2", "QueueMonitor", "Surveiller la file de sécurité",
             "passenger", "periodic", 20.0, 500.0, 500.0, "Moyenne"),
    TaskSpec("T3", "AIInference", "Prédire le risque de correspondance",
             "ai", "sporadic", 20.0, 100.0, 200.0, "Moyenne"),
    TaskSpec("T4", "DecisionResource", "Choisir l'action, réserver porte et bus",
             "decision", "sporadic", 10.0, 50.0, 100.0, "Haute"),
    TaskSpec("T5", "SecurityAlert", "Alerte de surcharge de file",
             "passenger", "sporadic", 2.0, 100.0, 50.0, "Critique"),
    TaskSpec("T6", "HistoryRetrain", "Réentraîner le modèle (Cloud)",
             "cloud", "aperiodic", None, None, None, "Basse"),
)


def load_task_table(path: Path = MEASURED_WCET_PATH) -> tuple[TaskSpec, ...]:
    """Renvoie la table v0 où les C mesurés remplacent les C estimés."""
    if not path.exists():
        return TASKS_V0
    measured = json.loads(path.read_text(encoding="utf-8"))["tasks"]
    return tuple(
        replace(task, c_ms=measured[task.task_id]["c_ms"], c_source="mesuré")
        if task.task_id in measured else task
        for task in TASKS_V0
    )


def total_utilization(tasks: tuple[TaskSpec, ...]) -> float:
    """Calcule U = somme des C/P (sporadiques prises à leur intervalle minimal)."""
    return sum(task.utilization for task in tasks)


def get_task(task_id: str, tasks: tuple[TaskSpec, ...] | None = None) -> TaskSpec:
    """Renvoie la tâche ``task_id`` de la table courante."""
    for task in tasks if tasks is not None else load_task_table():
        if task.task_id == task_id:
            return task
    raise KeyError(task_id)


def main() -> None:
    """Affiche la table courante et son facteur d'utilisation."""
    tasks = load_task_table()
    print(f"{'Tâche':<20}{'Type':<11}{'C (ms)':>10}{'P (ms)':>8}{'D (ms)':>8}  Source")
    for t in tasks:
        c = "-" if t.c_ms is None else f"{t.c_ms:.3f}"
        p = "-" if t.period_ms is None else f"{t.period_ms:.0f}"
        d = "-" if t.deadline_ms is None else f"{t.deadline_ms:.0f}"
        print(f"{t.task_id + ' ' + t.name:<20}{t.kind:<11}{c:>10}{p:>8}{d:>8}  {t.c_source}")
    print(f"U = {total_utilization(tasks):.4f}")


if __name__ == "__main__":
    main()

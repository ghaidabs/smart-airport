"""Système de mesure M3 : vraies tâches T1, T2, T3, T5 et mock de T4.

T3 exécute le vrai modèle de P2. T4 (dispatcher de P5) n'est pas livrée : son mock
consomme le C v0 (10 ms de CPU). Le réseau est remplacé par des tampons en mémoire :
ces temps n'incluent pas TCP. ``python -m rt.report`` mesure le système réel.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai.node_logic import AINode
from ai.predictor import RiskPredictor
from rt.metrics import MetricsCollector
from rt.monitors import (FlightMonitor, FlightRecord, FlightTable, QueueMonitor,
                         SecurityAlert, make_message, reference_flights)
from rt.periodic import PeriodicTask
from rt.sporadic import QueueEventSource, SporadicTask
from rt.tasks import TASKS_V0, TaskSpec, load_task_table

RESULTS_DIR = Path(__file__).with_name("results")
SEED = 42
BASE_FLIGHT_EVENT_MS = 500.0
BASE_QUEUE_EVENT_MS = 2000.0
GATES = ("G12", "G15", "G18", "G20")


def busy_cpu(duration_ms: float) -> None:
    """Consomme ``duration_ms`` de temps CPU du thread, hors préemptions."""
    end = time.thread_time_ns() + round(duration_ms * 1_000_000)
    while time.thread_time_ns() < end:
        pass


def sleep_until(target_ns: int) -> None:
    """Attend une date absolue de l'horloge ``perf_counter_ns``."""
    while (remaining := target_ns - time.perf_counter_ns()) > 0:
        time.sleep(remaining / 1e9)


def all_flights() -> list[FlightRecord]:
    """Vols de référence et 20 vols synthétiques TU8xx."""
    synthetic = [FlightRecord(f"TU8{i:02d}", "G12", "G12", 60, 500, 20, 200, 10)
                 for i in range(20)]
    return reference_flights() + synthetic


@dataclass
class M3System:
    """Tâches et états partagés d'une exécution."""

    metrics: MetricsCollector
    table: FlightTable
    queue: QueueMonitor
    t3_source: QueueEventSource
    t4_source: QueueEventSource
    tasks: list[Any]

    def start(self) -> None:
        """Démarre toutes les tâches."""
        for task in self.tasks:
            task.start()

    def stop(self) -> None:
        """Arrête les tâches et attend leur fin."""
        for task in self.tasks:
            task.stop()
        for task in self.tasks:
            task.join(timeout=3)


def build_system(origin_ns: int | None = None, real_t3: bool = True) -> M3System:
    """Assemble la chaîne T1 -> T3 -> T4 et la chaîne T2 -> T5.

    ``real_t3=False`` remplace T3 par son C v0 (20 ms) : seul le Gantt mis en scène
    l'utilise, car une inférence réelle est trop courte pour y être visible.
    """
    spec = {task.task_id: task for task in load_task_table()}
    v0 = {task.task_id: task for task in TASKS_V0}
    metrics = MetricsCollector()
    table = FlightTable(all_flights())
    t3_source = QueueEventSource(1000)
    t4_source = QueueEventSource(1000)
    t5_source = QueueEventSource(1000)
    logic = AINode(RiskPredictor(), log=lambda *_: None) if real_t3 else None

    def send(dest: str, msg: dict) -> None:
        if dest == "ai":
            t3_source.put(msg, timeout=0)

    def t3_body(event: dict) -> None:
        results: list[dict] = []
        if logic is not None:
            logic.handle(event, send=results.append)
        else:
            busy_cpu(v0["T3"].c_ms)
        # Un job de T4 par lot : le dispatcher de P5 décidera du traitement par vol.
        t4_source.put(make_message("RISK_RESULT", "ai", "decision", event["corr_id"],
                                   {"results": len(results)}), timeout=0)

    def t4_mock(_event: dict) -> None:
        busy_cpu(spec["T4"].c_ms)

    queue = QueueMonitor(lambda msg: t5_source.put(msg, timeout=0), seed=SEED)
    tasks = [
        _periodic(spec["T1"], FlightMonitor(table, send), metrics, origin_ns),
        _periodic(spec["T2"], queue, metrics, origin_ns),
        _sporadic(spec["T3"], t3_body, t3_source, metrics),
        _sporadic(spec["T4"], t4_mock, t4_source, metrics),
        _sporadic(spec["T5"], SecurityAlert(lambda _dest, _msg: None), t5_source, metrics),
    ]
    return M3System(metrics, table, queue, t3_source, t4_source, tasks)


def _periodic(spec: TaskSpec, body: Any, metrics: MetricsCollector,
              origin_ns: int | None) -> PeriodicTask:
    return PeriodicTask(spec.task_id, spec.period_ms, spec.deadline_ms, body,
                        metrics=metrics, origin_ns=origin_ns)


def _sporadic(spec: TaskSpec, body: Any, source: QueueEventSource,
              metrics: MetricsCollector) -> SporadicTask:
    return SporadicTask(spec.task_id, spec.period_ms, spec.deadline_ms, body, source,
                        metrics=metrics)


def generate_load(system: M3System, duration_s: float, load_factor: float,
                  seed: int = SEED) -> int:
    """Injecte des retards, portes et files. Renvoie le nombre d'événements."""
    rng = random.Random(seed)
    flights = [record.flight for record in all_flights()]
    start = time.perf_counter_ns()
    end = start + round(duration_s * 1e9)
    flight_mean_ns = BASE_FLIGHT_EVENT_MS * 1e6 / load_factor
    queue_mean_ns = BASE_QUEUE_EVENT_MS * 1e6 / load_factor
    next_flight = start + rng.expovariate(1 / flight_mean_ns)
    next_queue = start + rng.expovariate(1 / queue_mean_ns)
    count = 0
    while True:
        target = min(next_flight, next_queue)
        if target >= end:
            break
        sleep_until(int(target))
        count += 1
        corr_id = f"load-{count:06d}"
        if next_flight <= next_queue:
            flight = rng.choice(flights)
            if rng.random() < 0.1:
                system.table.set_gate(flight, rng.choice(GATES), corr_id)
            else:
                system.table.set_delay(flight, round(rng.uniform(6, 60), 1), corr_id)
            next_flight += rng.expovariate(1 / flight_mean_ns)
        else:
            system.queue.set_queue(rng.randint(60, 160), corr_id)
            next_queue += rng.expovariate(1 / queue_mean_ns)
    sleep_until(end)
    return count

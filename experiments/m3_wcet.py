"""Mesure du WCET de T1, T2 et T5 : 1000 exécutions seules, max x 1,2.

Chaque exécution suit le chemin le plus coûteux : tous les vols changent pour
T1, et une alerte part à chaque mesure de T2. L'envoi réseau est un stub, donc
ces C sont à remesurer après l'intégration de ``common.net`` (P1).

Lancement : ``python -m experiments.m3_wcet``
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from typing import Callable

from experiments.m3_common import RESULTS_DIR, all_flights
from rt.monitors import FlightMonitor, FlightTable, QueueMonitor, SecurityAlert, make_message
from rt.system import pin_to_cpu
from rt.tasks import MEASURED_WCET_PATH, load_task_table, total_utilization

MARGIN = 1.2


def measure(prepare: Callable[[int], None], body: Callable[[], None], runs: int) -> list[int]:
    """Renvoie les durées de ``body`` en ns. ``prepare`` n'est pas chronométré."""
    samples = []
    for index in range(runs):
        prepare(index)
        start = time.perf_counter_ns()
        body()
        samples.append(time.perf_counter_ns() - start)
    return samples


def bench_t1(runs: int) -> list[int]:
    """T1 sur 23 vols, tous modifiés, avec un ``corr_id`` distinct par vol."""
    flights = [record.flight for record in all_flights()]
    table = FlightTable(all_flights())
    monitor = FlightMonitor(table, lambda _dest, _msg: None)

    def prepare(index: int) -> None:
        for number, flight in enumerate(flights):
            table.set_delay(flight, 20 + index % 2, f"evt-{number}")

    return measure(prepare, monitor, runs)


def bench_t2(runs: int) -> list[int]:
    """T2 sur le chemin avec alerte : la file repasse sous le seuil entre deux mesures."""
    monitor = QueueMonitor(lambda _msg: None)

    def prepare(_index: int) -> None:
        monitor.set_queue(50)
        monitor()
        monitor.set_queue(150, "evt")

    return measure(prepare, monitor, runs)


def bench_t5(runs: int) -> list[int]:
    """T5 sur une alerte complète."""
    alert = SecurityAlert(lambda _dest, _msg: None)
    event = make_message("SECURITY_ALERT", "passenger", "passenger", "evt",
                         {"queue_len": 150, "wait_min": 15.0})
    return measure(lambda _index: None, lambda: alert(event), runs)


def main() -> None:
    """Mesure, écrit ``rt/measured_wcet.json`` et la table finale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1000)
    args = parser.parse_args()
    pinned = pin_to_cpu(0)

    measured = {}
    for task_id, bench in (("T1", bench_t1), ("T2", bench_t2), ("T5", bench_t5)):
        samples = bench(args.runs)
        max_ms = max(samples) / 1e6
        measured[task_id] = {
            "c_ms": round(max_ms * MARGIN, 4),
            "max_ms": round(max_ms, 4),
            "mean_ms": round(sum(samples) / len(samples) / 1e6, 4),
            "min_ms": round(min(samples) / 1e6, 4),
            "runs": args.runs,
        }
    MEASURED_WCET_PATH.write_text(json.dumps({
        "meta": {"margin": MARGIN, "pinned_cpu0": pinned,
                 "note": "Corps seuls, envoi réseau exclu. T3 et T4 restent en v0."},
        "tasks": measured,
    }, indent=2) + "\n", encoding="utf-8")

    tasks = load_task_table()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / "m3_task_table.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("task", "name", "node", "kind", "c_ms", "period_ms", "deadline_ms",
                         "criticality", "c_source", "utilization"))
        for t in tasks:
            writer.writerow((t.task_id, t.name, t.node, t.kind, t.c_ms, t.period_ms,
                             t.deadline_ms, t.criticality, t.c_source,
                             f"{t.utilization:.6f}"))
    for task_id, row in measured.items():
        print(f"{task_id}: min {row['min_ms']} moy {row['mean_ms']} max {row['max_ms']} "
              f"=> C = {row['c_ms']} ms")
    print(f"U = {total_utilization(tasks):.4f} (épinglé CPU0 : {pinned})")


if __name__ == "__main__":
    main()

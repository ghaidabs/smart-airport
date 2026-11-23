"""Expérience M3 : 60 s de charge normale, 60 s de charge x 2, Gantt réel de TU701.

Les phases de charge utilisent le vrai modèle pour T3. Le Gantt est une courte
exécution mise en scène, avec T3 et T4 à leur C v0 : TU703 (T3) et la panne G12 (T4)
sont injectés 16 ms avant le réveil de T1 qui détecte TU701, pour rendre visible
l'entrelacement de T1, T3 et T4 sur un seul cœur.

Lancement : ``python -m experiments.m3_load [--duration 60] [--fifo] [--out-dir DIR]``
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from experiments.m3_common import (RESULTS_DIR, M3System, build_system,  # noqa: E402
                                   generate_load, sleep_until)
from rt.metrics import NS_PER_MS, JobRecord, format_summary, write_summary_csv  # noqa: E402
from rt.monitors import make_message  # noqa: E402
from rt.real_gantt import plot_gantt, read_jobs_csv  # noqa: E402
from rt.system import enable_fifo, pin_to_cpu  # noqa: E402
from rt.tasks import get_task  # noqa: E402

GANTT_ATTEMPTS = 5


def run_phase(label: str, load_factor: float, duration_s: float) -> list[dict]:
    """Exécute une phase, écrit ses CSV et renvoie son résumé."""
    system = build_system()
    system.start()
    events = generate_load(system, duration_s, load_factor)
    system.stop()
    rows = system.metrics.summary()
    system.metrics.write_jobs_csv(RESULTS_DIR / f"m3_jobs_{label}.csv")
    write_summary_csv(rows, RESULTS_DIR / f"m3_summary_{label}.csv",
                      {"load": label, "duration_s": duration_s, "events": events})
    print(f"\n=== {label} : {events} événements en {duration_s:.0f} s ===")
    print(format_summary(rows))
    return rows


def plot_response_ratio(normal: list[dict], doubled: list[dict]) -> None:
    """Compare Rmax à la deadline de chaque tâche, en pourcentage."""
    surface, ink, secondary, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
    tasks = [row["task"] for row in normal]
    ratio = lambda rows: [100 * r["r_max_ms"] / get_task(r["task"]).deadline_ms for r in rows]
    series = (("Charge normale", ratio(normal), "#2a78d6", 0.19),
              ("Charge x 2", ratio(doubled), "#eb6834", -0.19))
    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=surface)
    ax.set_facecolor(surface)
    positions = list(range(len(tasks)))[::-1]
    for label, values, color, shift in series:
        bars = ax.barh([p + shift for p in positions], values, height=0.34, color=color,
                       label=label)
        for bar, value in zip(bars, values):
            ax.text(bar.get_width() + 1.2, bar.get_y() + bar.get_height() / 2, f"{value:.0f} %",
                    va="center", fontsize=8, color=secondary)
    ax.axvline(100, color="#d03b3b", linewidth=1.2)
    top = max(max(s[1]) for s in series)
    ax.set_xlim(0, max(112, top * 1.12))
    ax.text(100, len(tasks) - 0.42, "Deadline", ha="center", fontsize=8, color=secondary)
    ax.set_yticks(positions, tasks, color=ink)
    ax.set_xlabel("Rmax en pourcentage de la deadline", color=secondary)
    ax.set_title("Temps de réponse maximal par tâche", loc="left", color=ink, fontsize=12,
                 pad=16)
    ax.tick_params(axis="x", colors=muted)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=grid, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.legend(loc="lower right", frameon=False, labelcolor=secondary)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "m3_response_times.png", dpi=150, facecolor=surface)
    plt.close(fig)


def staged_tu701() -> tuple[M3System, int, bool]:
    """Rejoue TU701 et vérifie la détection par T1 et le recouvrement T1/T3/T4.

    Le retard est écrit avant de lancer les charges : sinon le GIL peut retarder
    l'injecteur au-delà du réveil de T1, qui ne verrait TU701 qu'au tick suivant.
    """
    origin = time.perf_counter_ns() + 200 * NS_PER_MS
    release = origin + 10 * 100 * NS_PER_MS
    system = build_system(origin_ns=origin, real_t3=False)
    system.start()
    sleep_until(release - 30 * NS_PER_MS)
    system.table.set_delay("TU701", 20, "evt-0001")
    sleep_until(release - 16 * NS_PER_MS)
    system.t4_source.put(make_message("GATE_UNAVAILABLE", "injector", "decision", "evt-0002",
                                      {"gate": "G12"}))
    system.t3_source.put(make_message("RISK_REQUEST", "flight", "ai", "evt-0003", {}))
    sleep_until(release + 400 * NS_PER_MS)
    system.stop()

    records = system.metrics.records()
    t1 = next((r for r in records if r.task == "T1" and r.release_ns == release), None)
    if t1 is None:
        return system, release, False
    detected = any(r.task == "T3" and r.corr_id == "evt-0001"
                   and t1.start_ns <= r.release_ns <= t1.end_ns for r in records)
    overlap = any(max(t1.start_ns, t3.start_ns, t4.start_ns) < min(t1.end_ns, t3.end_ns, t4.end_ns)
                  for t3 in records if t3.task == "T3"
                  for t4 in records if t4.task == "T4")
    return system, release, detected and overlap


def make_gantt() -> None:
    """Produit le Gantt réel de l'événement TU701."""
    for attempt in range(1, GANTT_ATTEMPTS + 1):
        system, release, overlap = staged_tu701()
        if overlap:
            break
    system.metrics.write_jobs_csv(RESULTS_DIR / "m3_jobs_tu701.csv")
    draw_gantt(system.metrics.records())
    print(f"\nGantt TU701 : recouvrement T1/T3/T4 = {overlap} (essai {attempt})")


def draw_gantt(records: list[JobRecord]) -> None:
    """Trace le Gantt de TU701 à partir de jobs mesurés."""
    plot_gantt(records, RESULTS_DIR / "m3_gantt_tu701.png", corr_id="evt-0001",
               title="Retard de TU701 : de la détection à la décision (exécution mise en scène)")


def main() -> None:
    """Lance les deux phases, le graphique et le Gantt."""
    global RESULTS_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--fifo", action="store_true",
                        help="priorités SCHED_FIFO par tâche, arrêt si elles sont refusées")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR,
                        help="dossier des résultats, pour conserver l'exécution de référence")
    parser.add_argument("--gantt-only", action="store_true",
                        help="redessine le Gantt depuis m3_jobs_tu701.csv, sans nouvelle mesure")
    args = parser.parse_args()
    RESULTS_DIR = args.out_dir
    if args.gantt_only:
        draw_gantt(read_jobs_csv(RESULTS_DIR / "m3_jobs_tu701.csv"))
        return
    if args.fifo and not enable_fifo():
        raise SystemExit("SCHED_FIFO refusé : augmenter RLIMIT_RTPRIO (voir rt/README.md).")
    pinned = pin_to_cpu(0)
    print(f"Épinglé CPU0 : {pinned} ; SCHED_FIFO par tâche : {args.fifo}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    normal = run_phase("normal", 1.0, args.duration)
    doubled = run_phase("x2", 2.0, args.duration)
    plot_response_ratio(normal, doubled)
    make_gantt()


if __name__ == "__main__":
    main()

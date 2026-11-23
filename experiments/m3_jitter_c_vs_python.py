"""Bonus M3 : gigue de la même tâche périodique en Python et en C.

Paramètres communs : période 10 ms, 2000 jobs, 200 µs de CPU, cœur 0.

Lancement (Linux) : ``python -m experiments.m3_jitter_c_vs_python``
"""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from experiments.m3_common import RESULTS_DIR, busy_cpu  # noqa: E402
from rt.metrics import MetricsCollector  # noqa: E402
from rt.periodic import PeriodicTask  # noqa: E402
from rt.system import pin_to_cpu  # noqa: E402

C_DIR = Path(__file__).resolve().parents[1] / "rt" / "periodic_c"
PERIOD_MS = 10.0
JOBS = 2000
BUSY_US = 200.0


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Percentile par rang le plus proche."""
    index = min(len(sorted_values) - 1, max(0, round(fraction * len(sorted_values)) - 1))
    return sorted_values[index]


def stats(jitter_us: list[float]) -> dict[str, float]:
    """Calcule moyenne, p50, p99 et max de la gigue."""
    values = sorted(jitter_us)
    return {"mean_us": sum(values) / len(values), "p50_us": percentile(values, 0.50),
            "p99_us": percentile(values, 0.99), "max_us": values[-1]}


def run_python() -> list[float]:
    """Mesure la version Python."""
    metrics = MetricsCollector()
    task = PeriodicTask("py", PERIOD_MS, PERIOD_MS, lambda: busy_cpu(BUSY_US / 1000),
                        metrics=metrics, max_jobs=JOBS, offset_ms=100)
    task.start()
    task.join()
    return [r.jitter_ms * 1000 for r in metrics.records()]


def run_c() -> list[float]:
    """Compile et mesure la version C."""
    # -B : recompile toujours, car un binaire copié peut avoir perdu son bit exécutable.
    subprocess.run(["make", "-B", "-C", str(C_DIR), "periodic"], check=True, capture_output=True)
    out = RESULTS_DIR / "m3_jitter_c_jobs.csv"
    subprocess.run([str(C_DIR / "periodic"), str(PERIOD_MS), str(JOBS), str(BUSY_US),
                    str(out), "0"], check=True)
    with out.open(encoding="utf-8") as handle:
        return [(int(r["start_ns"]) - int(r["release_ns"])) / 1000 for r in csv.DictReader(handle)]


def plot(results: dict[str, dict[str, float]]) -> None:
    """Compare p50, p99 et max sur une échelle logarithmique."""
    surface, ink, secondary, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
    keys = (("p50_us", "p50"), ("p99_us", "p99"), ("max_us", "max"))
    styles = {"Python": ("#2a78d6", "o"), "C": ("#eb6834", "s")}
    fig, ax = plt.subplots(figsize=(8, 3.6), facecolor=surface)
    ax.set_facecolor(surface)
    for language, values in results.items():
        color, marker = styles[language]
        offset = 0.12 if language == "Python" else -0.12
        xs = [values[key] for key, _ in keys]
        ys = [2 - i + offset for i in range(len(keys))]
        ax.scatter(xs, ys, color=color, marker=marker, s=64, label=language, zorder=3)
        for x, y in zip(xs, ys):
            ax.annotate(f"{x:.0f} µs", (x, y), xytext=(8, -3), textcoords="offset points",
                        fontsize=8, color=secondary)
    ax.set_xscale("log")
    ax.margins(x=0.16)
    ax.set_yticks([2, 1, 0], [label for _, label in keys], color=ink)
    ax.set_ylim(-0.5, 2.5)
    ax.set_xlabel("Gigue de démarrage (µs, échelle log)", color=secondary)
    ax.set_title("Gigue d'une tâche de 10 ms : Python et C", loc="left", color=ink, fontsize=12)
    ax.tick_params(axis="x", which="both", colors=muted)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=grid, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=2, frameon=False,
              labelcolor=secondary)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "m3_jitter_c_vs_python.png", dpi=150, facecolor=surface)
    plt.close(fig)


def main() -> None:
    """Exécute les deux versions et écrit le comparatif."""
    if not pin_to_cpu(0):
        raise SystemExit("Cette expérience nécessite Linux ou WSL2.")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = {"Python": stats(run_python()), "C": stats(run_c())}
    with (RESULTS_DIR / "m3_jitter_c_vs_python.csv").open("w", newline="",
                                                          encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("language", "mean_us", "p50_us", "p99_us", "max_us"))
        for language, row in results.items():
            writer.writerow((language, *(f"{row[k]:.3f}" for k in
                                         ("mean_us", "p50_us", "p99_us", "max_us"))))
            print(f"{language}: " + ", ".join(f"{k}={v:.1f}" for k, v in row.items()))
    plot(results)


if __name__ == "__main__":
    main()

"""Diagramme de Gantt réel d'une chaîne d'événement, construit à partir des jobs mesurés.

Le bandeau du haut décompose le temps de bout en bout. Le Gantt du bas situe chaque job.
Une barre va du début à la fin d'un job : elle inclut donc les préemptions. Deux barres
superposées dans le temps sont entrelacées sur le cœur, pas exécutées en parallèle. Les
jobs très courts sont élargis à l'affichage ; leurs durées exactes sont dans le CSV.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from rt.metrics import NS_PER_MS, JobRecord  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CHAIN = "#2a78d6"
WAIT = "#eb6834"
OTHER = "#c3c2b7"
CRITICAL = "#d03b3b"
MIN_BAR_MS = 1.0  # Largeur d'affichage minimale : un job de T1 dure moins de 0,2 ms.


def read_jobs_csv(path: Path) -> list[JobRecord]:
    """Relit un fichier ``m3_jobs_*.csv``."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [JobRecord(r["task"], int(r["job"]), int(r["release_ns"]), int(r["start_ns"]),
                          int(r["end_ns"]), int(r["deadline_ns"]), int(r["cpu_ns"]),
                          int(r["skipped_after"]), bool(int(r["error"])), r["corr_id"])
                for r in csv.DictReader(handle)]


def find_chain(records: Iterable[JobRecord], corr_id: str) -> tuple[JobRecord, JobRecord, JobRecord]:
    """Renvoie les jobs T1, T3, T4 de la chaîne ``corr_id``.

    Un job périodique n'a pas de ``corr_id`` : T1 est le dernier job démarré avant
    l'arrivée de la requête dans T3.
    """
    jobs = list(records)
    try:
        t3 = next(r for r in jobs if r.task == "T3" and r.corr_id == corr_id)
        t4 = next(r for r in jobs if r.task == "T4" and r.corr_id == corr_id)
        t1 = max((r for r in jobs if r.task == "T1" and r.start_ns <= t3.release_ns),
                 key=lambda r: r.start_ns)
    except (StopIteration, ValueError) as exc:
        raise ValueError(f"chaîne T1 -> T3 -> T4 incomplète pour {corr_id}") from exc
    return t1, t3, t4


def _ms(value_ns: int, t0_ns: int) -> float:
    return (value_ns - t0_ns) / NS_PER_MS


def _fr(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _draw_breakdown(ax, t1: JobRecord, t3: JobRecord, t4: JobRecord, t0_ns: int) -> None:
    """Décompose le temps de bout en bout en attentes et exécutions."""
    edges = [0.0, _ms(t1.release_ns, t0_ns), _ms(t3.release_ns, t0_ns),
             _ms(t3.start_ns, t0_ns), _ms(t3.end_ns, t0_ns), _ms(t4.end_ns, t0_ns)]
    stages = (("Attente du tick T1", WAIT), ("T1", CHAIN), ("T3 retenue par l'intervalle min.", WAIT),
              ("T3", CHAIN), ("T4", CHAIN))
    for (label, color), left, right in zip(stages, edges, edges[1:]):
        hatch = "////" if color == WAIT else None
        ax.barh(0, right - left, left=left, height=0.56, facecolor=SURFACE if hatch else color,
                edgecolor=color, hatch=hatch, linewidth=0.8)
        width = right - left
        name = f"{label}\n{_fr(width)} ms"
        if width >= 15:
            ax.text((left + right) / 2, 0, name, ha="center", va="center", fontsize=7.5,
                    color=INK if hatch else "white", linespacing=1.15,
                    bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.2) if hatch else None)
        else:
            ax.annotate(name.replace("\n", " : "), xy=((left + right) / 2, -0.28),
                        xytext=((left + right) / 2, -0.52), ha="center", va="top", fontsize=7.5,
                        color=INK_SECONDARY, annotation_clip=False,
                        arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.6))
    total = edges[-1]
    ax.annotate("", xy=(0, 0.48), xytext=(total, 0.48),
                arrowprops=dict(arrowstyle="<->", color=INK_SECONDARY, linewidth=0.8))
    ax.text(total / 2, 0.56, f"Événement → décision : {_fr(total)} ms", ha="center", va="bottom",
            fontsize=9.5, color=INK, fontweight="bold")
    ax.set_ylim(-0.75, 0.95)
    ax.axis("off")


def _draw_jobs(ax, jobs: Sequence[JobRecord], chain: tuple[JobRecord, JobRecord, JobRecord],
               rows: dict[str, int], t0_ns: int, x_max: float) -> None:
    """Trace attentes, exécutions, arrivées et deadlines de la chaîne."""
    for job in jobs:
        y = rows[job.task]
        in_chain = job in chain
        start = _ms(job.start_ns, t0_ns)
        width = max(_ms(job.end_ns, job.start_ns), MIN_BAR_MS)
        release = _ms(job.release_ns, t0_ns)
        if in_chain and start - release >= 3 * MIN_BAR_MS:
            ax.barh(y, start - release, left=release, height=0.46, facecolor=SURFACE,
                    edgecolor=WAIT, hatch="////", linewidth=0.7, zorder=2)
        color = CRITICAL if job.missed else CHAIN if in_chain else OTHER
        ax.barh(y, width, left=start, height=0.46, color=color, linewidth=0, zorder=3)
        if 0 <= release <= x_max and job not in chain[1:]:  # Une flèche marque déjà ces arrivées.
            ax.plot(release, y + 0.36, marker="v", markersize=4.5, color=INK_SECONDARY,
                    linestyle="none", zorder=4)
        if not in_chain and job.corr_id:
            ax.text(start + width / 2, y, job.corr_id, ha="center", va="center", fontsize=6.5,
                    color=INK_SECONDARY, zorder=5)

    for job in chain[1:]:
        deadline = _ms(job.deadline_ns, t0_ns)
        if deadline <= x_max:
            y = rows[job.task]
            ax.plot([deadline, deadline], [y - 0.3, y + 0.3], color=CRITICAL, linewidth=1.4, zorder=4)
            ax.text(deadline + 1.2, y, f"deadline {job.task}\nmarge {_fr(_ms(job.deadline_ns, job.end_ns))} ms",
                    va="center", fontsize=7, color=INK_SECONDARY, linespacing=1.1)

    for source, target in zip(chain, chain[1:]):
        ax.annotate("", xy=(_ms(target.release_ns, t0_ns), rows[target.task] + 0.25),
                    xytext=(_ms(source.end_ns, t0_ns), rows[source.task] - 0.25),
                    arrowprops=dict(arrowstyle="-|>", color=INK, linewidth=1.0,
                                    shrinkA=0, shrinkB=0), zorder=6)

    interleaved = _overlap(jobs)
    if interleaved:
        left, right = (_ms(v, t0_ns) for v in interleaved)
        low, high = sorted((rows["T3"], rows["T4"]))
        ax.fill_between([left, right], low - 0.34, high + 0.34, color=GRID, alpha=0.6,
                        linewidth=0, zorder=1)
        ax.text(right + 1.5, rows["T4"], "T3 et T4 entrelacés\nsur un seul cœur",
                ha="left", va="center", fontsize=7, color=INK_SECONDARY, linespacing=1.1)


def _overlap(jobs: Sequence[JobRecord]) -> tuple[int, int] | None:
    """Renvoie le premier recouvrement entre un job de T3 et un job de T4."""
    for t3 in (j for j in jobs if j.task == "T3"):
        for t4 in (j for j in jobs if j.task == "T4"):
            left, right = max(t3.start_ns, t4.start_ns), min(t3.end_ns, t4.end_ns)
            if left < right:
                return left, right
    return None


def plot_gantt(records: Iterable[JobRecord], out_path: Path, *, corr_id: str,
               before_ms: float = 30.0, after_ms: float = 245.0,
               tasks: Sequence[str] = ("T1", "T3", "T4", "T2", "T5"),
               title: str = "Gantt réel") -> None:
    """Trace la chaîne ``corr_id`` autour du réveil de T1 qui l'a détectée."""
    all_jobs = list(records)
    chain = find_chain(all_jobs, corr_id)
    t0_ns = chain[0].release_ns - round(before_ms * NS_PER_MS)
    x_max = before_ms + after_ms
    t1_ns = t0_ns + round(x_max * NS_PER_MS)
    jobs = [r for r in all_jobs if r.task in tasks and r.end_ns >= t0_ns and r.start_ns <= t1_ns]
    active = [task for task in tasks if any(j.task == task for j in jobs)]
    rows = {task: index for index, task in enumerate(reversed(active))}

    fig, (top, ax) = plt.subplots(2, 1, figsize=(11, 6), sharex=True, facecolor=SURFACE,
                                  gridspec_kw={"height_ratios": [1.05, 3.4], "hspace": 0.2})
    top.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    _draw_breakdown(top, *chain, t0_ns)
    _draw_jobs(ax, jobs, chain, rows, t0_ns, x_max)

    ax.set_yticks(list(rows.values()), list(rows.keys()), color=INK)
    ax.set_xlim(0, x_max)
    ax.set_ylim(-0.6, len(rows) - 0.05)
    ax.set_xlabel("Temps depuis l'injection de l'événement (ms)", color=INK_SECONDARY)
    ax.tick_params(axis="x", colors=MUTED)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)

    handles = [Patch(color=CHAIN, label=f"Exécution {corr_id}"),
               Patch(facecolor=SURFACE, edgecolor=WAIT, hatch="////", label="Attente"),
               Patch(color=OTHER, label="Autres jobs"),
               Line2D([], [], marker="v", color=INK_SECONDARY, linestyle="none", markersize=5,
                      label="Réveil ou arrivée"),
               Line2D([], [], color=CRITICAL, linewidth=1.4, label="Deadline")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.15),
              ncol=len(handles), frameon=False, labelcolor=INK_SECONDARY, fontsize=8.5)
    fig.suptitle(title, x=0.055, y=0.975, ha="left", color=INK, fontsize=12.5)
    missing = [task for task in tasks if task not in active]
    note = "T3 et T4 sont mis en scène à leur C v0. Les jobs très courts sont élargis à 1 ms."
    if missing:
        note += f" Aucun job de {', '.join(missing)} dans cette fenêtre."
    fig.text(0.055, 0.012, note, fontsize=7.5, color=MUTED)
    fig.subplots_adjust(left=0.055, right=0.985, top=0.91, bottom=0.18)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor=SURFACE)
    plt.close(fig)

"""Diagramme de Gantt réel, construit à partir des jobs mesurés.

Une barre va du début à la fin d'un job : elle inclut donc les préemptions.
Deux barres superposées dans le temps signifient que les jobs sont entrelacés
sur le cœur, pas qu'ils s'exécutent en parallèle. Les jobs très courts sont
élargis à l'affichage ; leurs durées exactes sont dans le CSV.
"""

from __future__ import annotations

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
HIGHLIGHT = "#2a78d6"
OTHER = "#c3c2b7"
CRITICAL = "#d03b3b"
MIN_BAR_MS = 1.0  # Largeur d'affichage minimale : un job de T1 dure moins de 0,2 ms.


def plot_gantt(records: Iterable[JobRecord], out_path: Path, *, t0_ns: int, t1_ns: int,
               tasks: Sequence[str], highlight_corr: str | None = None,
               title: str = "Gantt réel") -> None:
    """Trace les jobs de ``tasks`` entre ``t0_ns`` et ``t1_ns`` (axe en ms)."""
    selected = [r for r in records if r.end_ns >= t0_ns and r.start_ns <= t1_ns]
    rows = {task: index for index, task in enumerate(reversed(list(tasks)))}

    fig, ax = plt.subplots(figsize=(11, 4.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for record in selected:
        if record.task not in rows:
            continue
        y = rows[record.task]
        start = (record.start_ns - t0_ns) / NS_PER_MS
        width = max((record.end_ns - record.start_ns) / NS_PER_MS, MIN_BAR_MS)
        color = CRITICAL if record.missed else (
            HIGHLIGHT if highlight_corr and record.corr_id == highlight_corr else OTHER)
        ax.barh(y, width, left=start, height=0.5, color=color, linewidth=0)
        release = (record.release_ns - t0_ns) / NS_PER_MS
        if 0 <= release <= (t1_ns - t0_ns) / NS_PER_MS:
            ax.plot(release, y + 0.38, marker="^", markersize=6, color=INK_SECONDARY,
                    linestyle="none")

    ax.set_yticks(list(rows.values()), list(rows.keys()), color=INK)
    ax.set_xlim(0, (t1_ns - t0_ns) / NS_PER_MS)
    ax.set_ylim(-0.6, len(rows) - 0.3)
    ax.set_xlabel("Temps depuis le début de la fenêtre (ms)", color=INK_SECONDARY)
    ax.set_title(title, loc="left", color=INK, fontsize=12)
    ax.tick_params(axis="x", colors=MUTED)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)

    handles = [Patch(color=OTHER, label="Autres jobs"),
               Patch(color=CRITICAL, label="Deadline dépassée"),
               Line2D([], [], marker="^", color=INK_SECONDARY, linestyle="none",
                      label="Réveil ou arrivée")]
    if highlight_corr:
        handles.insert(0, Patch(color=HIGHLIGHT, label=f"Chaîne {highlight_corr}"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.17),
              ncol=len(handles), frameon=False, labelcolor=INK_SECONDARY)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor=SURFACE)
    plt.close(fig)

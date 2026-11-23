"""Résumé des mesures T1 à T5 du système réel (``logs/rt/*_jobs.csv``).

Les nœuds écrivent ces fichiers à leur arrêt : lancer ``run_all.sh``, injecter un
scénario, faire Ctrl+C, puis ``python -m rt.report``.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from rt.metrics import JobRecord, format_summary, summarize, write_summary_csv
from rt.runtime import rt_log_dir
from rt.tasks import get_task


def read_jobs(directory: Path) -> list[JobRecord]:
    """Relit les jobs de tous les nœuds."""
    jobs: list[JobRecord] = []
    for path in sorted(directory.glob("*_jobs.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                jobs.append(JobRecord(
                    task=row["task"], job=int(row["job"]), release_ns=int(row["release_ns"]),
                    start_ns=int(row["start_ns"]), end_ns=int(row["end_ns"]),
                    deadline_ns=int(row["deadline_ns"]), cpu_ns=int(row["cpu_ns"]),
                    skipped_after=int(row["skipped_after"]), error=bool(int(row["error"])),
                    corr_id=row["corr_id"]))
    return jobs


def main(argv: list[str] | None = None) -> int:
    """Affiche le résumé, et l'écrit en CSV si ``--csv`` est donné."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=None, help="dossier des *_jobs.csv")
    parser.add_argument("--csv", type=Path, default=None, help="exporte le résumé")
    parser.add_argument("--label", default="live", help="valeur de la colonne load du CSV")
    args = parser.parse_args(argv)

    rows = summarize(read_jobs(args.dir or rt_log_dir()))
    if not rows:
        print("Aucune mesure : arrêter les nœuds (Ctrl+C) pour écrire logs/rt/.", file=sys.stderr)
        return 1
    print(format_summary(rows))
    for row in rows:
        deadline = get_task(str(row["task"])).deadline_ms
        print(f"{row['task']} : Rmax {row['r_max_ms']:.3f} ms / D {deadline:.0f} ms, "
              f"{row['misses']} dépassement(s), {row['errors']} erreur(s)")
    if args.csv:
        write_summary_csv(rows, args.csv, {"load": args.label})
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Chronologie fusionnée des journaux et temps de bout en bout (P1).

Usage ::

    python -m tools.timeline                 # chronologie + résumé par corr_id
    python -m tools.timeline --corr evt-ab12-0001
    python -m tools.timeline --summary       # résumé seul
    python -m tools.timeline --csv experiments/results/e2e.csv

Pour chaque ``corr_id`` :

- **bout en bout** : du premier événement (envoi par l'injecteur) à la première et à la
  dernière ligne ``DECISION`` du nœud Décision ;
- **réseau** : latence aller simple par lien, en appariant ``SEND`` et ``RECV`` par
  l'id du message (même machine, donc même horloge) ;
- **traitement par nœud** : pour chaque message reçu, temps jusqu'au premier envoi
  suivant du même nœud dans la même chaîne (approximation lisible pour la démo).
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from common import config

MSG_RE = re.compile(r"^(?P<type>\w+) (?P<src>\w+)->(?P<dst>\w+) id=(?P<id>\w+)")


@dataclass
class Row:
    ts_ns: int
    node: str
    thread: str
    corr_id: str
    event: str
    detail: str


def read_logs(log_dir: Path) -> list[Row]:
    rows: list[Row] = []
    for path in sorted(log_dir.glob("*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append(Row(int(r["ts_ns"]), r["node"], r["thread"],
                                    r["corr_id"], r["event"], r["detail"]))
                except (KeyError, ValueError, TypeError):
                    continue  # ligne tronquée (nœud tué en cours d'écriture)
    # À horodatage égal (horloge grossière, ex. Windows), l'envoi précède la réception.
    rank = {"SEND": 0, "RECV": 2}
    rows.sort(key=lambda r: (r.ts_ns, rank.get(r.event, 1)))
    return rows


def ms(ns: float) -> float:
    return ns / 1e6


def print_chronology(rows: list[Row], width: int = 90) -> None:
    if not rows:
        return
    t0 = rows[0].ts_ns
    print(f"{'t (ms)':>10}  {'nœud':<9} {'thread':<16} {'corr_id':<15} {'événement':<14} détail")
    for r in rows:
        detail = r.detail if len(r.detail) <= width else r.detail[:width - 1] + "…"
        print(f"{ms(r.ts_ns - t0):>10.3f}  {r.node:<9} {r.thread:<16} {r.corr_id:<15} "
              f"{r.event:<14} {detail}")


def summarize(rows: list[Row]) -> list[dict]:
    by_corr: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.corr_id:
            by_corr[r.corr_id].append(r)

    results = []
    for corr, chain in by_corr.items():
        # Événement initial : l'envoi par l'injecteur s'il existe.
        origin = next((r for r in chain if r.node == "injector" and r.event == "SEND"),
                      chain[0])
        t0 = origin.ts_ns
        decisions = [r for r in chain if r.event == "DECISION" and r.node == "decision"]
        sends = {}
        links: dict[str, list[float]] = defaultdict(list)
        processing: dict[str, list[float]] = defaultdict(list)
        for r in chain:
            m = MSG_RE.match(r.detail)
            if not m:
                continue
            if r.event == "SEND":
                sends[m["id"]] = r
            elif r.event == "RECV" and m["id"] in sends:
                links[f"{m['src']}->{m['dst']}"].append(ms(r.ts_ns - sends[m["id"]].ts_ns))
                nxt = next((s for s in chain if s.node == r.node and s.event == "SEND"
                            and s.ts_ns >= r.ts_ns), None)
                if nxt is not None:
                    processing[r.node].append(ms(nxt.ts_ns - r.ts_ns))
        results.append({
            "corr_id": corr,
            "first_event": origin.detail.split(" id=")[0],
            "n_decisions": len(decisions),
            "e2e_first_ms": ms(decisions[0].ts_ns - t0) if decisions else None,
            "e2e_last_ms": ms(decisions[-1].ts_ns - t0) if decisions else None,
            "links": {k: (statistics.mean(v), max(v), len(v)) for k, v in links.items()},
            "processing": {k: (statistics.mean(v), max(v), len(v)) for k, v in processing.items()},
        })
    return results


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f} ms"


def print_summary(results: list[dict]) -> None:
    for res in results:
        print(f"\n== {res['corr_id']}  ({res['first_event']})")
        print(f"   bout en bout : 1re décision {fmt(res['e2e_first_ms'])}, "
              f"dernière {fmt(res['e2e_last_ms'])} ({res['n_decisions']} décision(s))")
        for title, key in (("réseau (aller simple)", "links"),
                           ("traitement par nœud", "processing")):
            if res[key]:
                print(f"   {title} :")
                for name, (mean, worst, n) in sorted(res[key].items()):
                    print(f"     {name:<22} moy {mean:8.3f} ms   max {worst:8.3f} ms   n={n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-dir", type=Path, default=config.LOG_DIR)
    parser.add_argument("--corr", help="ne garder qu'un corr_id")
    parser.add_argument("--summary", action="store_true", help="résumé seul")
    parser.add_argument("--csv", type=Path, help="exporte le résumé bout en bout en CSV")
    opts = parser.parse_args(argv)

    rows = read_logs(opts.log_dir)
    if opts.corr:
        rows = [r for r in rows if r.corr_id == opts.corr]
    if not rows:
        print(f"aucune ligne dans {opts.log_dir}", file=sys.stderr)
        return 1
    if not opts.summary:
        print_chronology(rows)
    results = summarize(rows)
    print_summary(results)
    if opts.csv:
        opts.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(opts.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["corr_id", "first_event", "n_decisions", "e2e_first_ms", "e2e_last_ms"])
            for r in results:
                w.writerow([r["corr_id"], r["first_event"], r["n_decisions"],
                            r["e2e_first_ms"], r["e2e_last_ms"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())

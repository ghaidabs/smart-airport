"""Injecteur d'événements, interface pour le professeur (P1).

Exemples (depuis la racine du dépôt, nœuds lancés par ``run_all.sh``) ::

    python -m scenarios.inject tu701              # scénario de référence complet
    python -m scenarios.inject demo_final         # variante de la démo finale
    python -m scenarios.inject overload_x2        # démo finale, événements x 2
    python -m scenarios.inject play mon_scenario.json
    python -m scenarios.inject delay TU701 25
    python -m scenarios.inject gate_down G12
    python -m scenarios.inject queue 150
    python -m scenarios.inject tu701 --load x2 --rate 50

Temps : par défaut on respecte l'heure simulée du scénario (1 s réelle = 1 min
simulée). ``--rate N`` envoie à N messages par seconde, ``--fast`` sans attendre.

``--load xN`` : chaque événement ``delay`` est dupliqué sur N-1 vols synthétiques
(TU801, TU802...) et chaque ``queue`` est rejoué N fois. ``gate_down`` n'est pas
dupliqué (les portes sont réelles et en nombre fixe).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from common import config
from common.logger import CsvLogger
from common.net import Sender
from common.protocol import Message, MessageType as T

NAMED_SCENARIOS = ("tu701", "demo_final", "overload_x2")


@dataclass
class Event:
    at: str | None          # heure simulée "HH:MM", None pour une commande isolée
    cmd: str
    args: list


def build_message(ev: Event) -> Message:
    """Traduit une commande en message (annexe A)."""
    if ev.cmd == "delay":
        flight, minutes = ev.args[0], int(ev.args[1])
        payload = {"flight": flight, "delay_min": minutes}
        if ev.at:
            payload["sim_time"] = ev.at
        return Message(T.FLIGHT_DELAY, "injector", "flight", payload)
    if ev.cmd == "gate_down":
        return Message(T.GATE_UNAVAILABLE, "injector", "flight", {"gate": ev.args[0]})
    if ev.cmd == "queue":
        return Message(T.QUEUE_UPDATE, "injector", "passenger",
                       {"queue_len": int(ev.args[0])})
    raise ValueError(f"commande inconnue : {ev.cmd}")


def apply_load(events: list[Event], factor: int) -> list[Event]:
    """Multiplie les événements par ``factor`` (vols synthétiques TU8xx)."""
    out: list[Event] = []
    synth = 0
    for ev in events:
        out.append(ev)
        for _ in range(factor - 1):
            if ev.cmd == "delay":
                synth += 1
                out.append(Event(ev.at, "delay", [f"TU8{synth:02d}", ev.args[1]]))
            elif ev.cmd == "queue":
                out.append(Event(ev.at, "queue", list(ev.args)))
    return out


def sim_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def load_scenario(path: Path) -> tuple[list[Event], int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = [Event(e.get("at"), e["cmd"], e.get("args", [])) for e in data["events"]]
    return events, int(data.get("load", 1))


def parse_load(text: str) -> int:
    value = int(text.lower().lstrip("x"))
    if value < 1:
        raise argparse.ArgumentTypeError("--load doit valoir au moins x1")
    return value


def run(events: list[Event], rate: float | None, fast: bool) -> int:
    sender = Sender(CsvLogger("injector"))
    sent = 0
    start = time.monotonic()
    first_sim = next((sim_minutes(e.at) for e in events if e.at), None)
    try:
        for i, ev in enumerate(events):
            # Attente : rythme fixe (--rate), ou heure simulée du scénario.
            if rate:
                target = start + i / rate
            elif not fast and ev.at and first_sim is not None:
                target = start + (sim_minutes(ev.at) - first_sim) / config.SIM_MINUTES_PER_REAL_SECOND
            else:
                target = 0.0
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            msg = build_message(ev)
            msg.payload["t0_ns"] = time.time_ns()
            ok = sender.send(msg.dest, msg)
            sent += ok
            label = f"[{ev.at}] " if ev.at else ""
            status = "envoyé" if ok else "ÉCHEC (nœud lancé ?)"
            print(f"{label}{ev.cmd} {' '.join(map(str, ev.args))} -> {msg.dest} "
                  f"corr_id={msg.corr_id} : {status}", flush=True)
    finally:
        sender.close()
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", help=f"{', '.join(NAMED_SCENARIOS)}, play, delay, gate_down, queue")
    parser.add_argument("args", nargs="*", help="arguments de la commande")
    parser.add_argument("--load", type=parse_load, default=None, metavar="xN",
                        help="multiplie les événements (ex. x2)")
    parser.add_argument("--rate", type=float, default=None, metavar="N",
                        help="messages par seconde (ignore l'heure simulée)")
    parser.add_argument("--fast", action="store_true", help="envoie sans attendre")
    opts = parser.parse_intermixed_args(argv)

    load = 1
    if opts.command in NAMED_SCENARIOS:
        events, load = load_scenario(config.SCENARIO_DIR / f"{opts.command}.json")
    elif opts.command == "play":
        if len(opts.args) != 1:
            parser.error("play attend un fichier JSON")
        events, load = load_scenario(Path(opts.args[0]))
    elif opts.command in ("delay", "gate_down", "queue"):
        expected = {"delay": 2, "gate_down": 1, "queue": 1}[opts.command]
        if len(opts.args) != expected:
            parser.error(f"{opts.command} attend {expected} argument(s)")
        events = [Event(None, opts.command, opts.args)]
    else:
        parser.error(f"commande inconnue : {opts.command}")

    if opts.load is not None:
        load = opts.load
    events = apply_load(events, load)
    sent = run(events, opts.rate, opts.fast)
    print(f"{sent}/{len(events)} événement(s) envoyé(s)")
    return 0 if sent == len(events) else 1


if __name__ == "__main__":
    sys.exit(main())

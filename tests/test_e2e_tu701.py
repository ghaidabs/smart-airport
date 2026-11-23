"""Test d'intégration : le scénario TU701 traverse les 4 nœuds (P1).

Lance les 4 nœuds dans des processus séparés (ports 5001 à 5004), rejoue ``tu701``
sans attente et vérifie les décisions dans les journaux. À relancer après chaque
branchement de l'intégration en relais.
"""

from __future__ import annotations

import csv
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from common import config
from common.net import wait_for_nodes

ROOT = Path(__file__).resolve().parent.parent


def ports_busy() -> bool:
    for node in config.NODES:
        with socket.socket() as s:
            if s.connect_ex(config.address(node)) == 0:
                return True
    return False


def scenario_done(decisions: list[dict]) -> bool:
    levels = {d["flight"]: d for d in decisions}
    tu701 = levels.get("TU701", {})
    return (tu701.get("gate") == "G15" and None in levels
            and levels.get("TU703", {}).get("level") == "SURVEILLE" and "TU702" in levels)


@pytest.mark.skipif(ports_busy(), reason="des nœuds tournent déjà sur 5001-5004")
def test_tu701_end_to_end(tmp_path):
    env = os.environ | {"SA_LOG_DIR": str(tmp_path), "PYTHONPATH": str(ROOT)}
    procs = [subprocess.Popen([sys.executable, "-m", f"nodes.{n}_node"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             for n in config.NODES]
    try:
        assert wait_for_nodes(timeout=10), "les nœuds n'ont pas démarré"
        subprocess.run([sys.executable, "-m", "scenarios.inject", "tu701", "--fast"],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        decisions = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with open(tmp_path / "decision.csv", newline="", encoding="utf-8") as f:
                decisions = [json.loads(r["detail"]) for r in csv.DictReader(f)
                             if r["event"] == "DECISION"]
            if scenario_done(decisions):  # T1 peut regrouper les événements d'un même réveil
                break
            time.sleep(0.1)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=5)

    critical = [d for d in decisions if d["flight"] == "TU701"]
    assert critical and critical[-1]["level"] == "CRITIQUE"
    assert critical[-1]["gate"] == "G15"          # G12 indisponible -> G15
    assert "bus" in critical[-1]["actions"] and critical[-1]["buses"] == 2
    assert critical[-1]["latency_ms"] is not None
    levels = {d["flight"]: d["level"] for d in decisions}
    assert levels["TU703"] == "SURVEILLE" and levels["TU702"] == "AUCUNE"
    assert any(d["flight"] is None and "open_security_lane" in d["actions"] for d in decisions)
    for name in config.NODES:
        assert "HANDLER_ERROR" not in (tmp_path / f"{name}.csv").read_text(encoding="utf-8")
    jobs = set()
    for path in (tmp_path / "rt").glob("*_jobs.csv"):
        with path.open(newline="", encoding="utf-8") as f:
            jobs |= {r["task"] for r in csv.DictReader(f) if not int(r["error"])}
    assert {"T1", "T2", "T3", "T4", "T5"} <= jobs  # les 5 tâches ont réellement tourné

"""Configuration partagée par tous les nœuds (P1).

Valeurs fixées en section 4 du guide : 4 nœuds sur une machine, ports 5001 à 5004,
1 s réelle = 1 min simulée, graine 42. Les données du scénario de référence
(vols, portes) sont ici pour que tous les nœuds et l'injecteur utilisent les mêmes.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Nœuds et réseau -------------------------------------------------------

HOST: str = os.environ.get("SA_HOST", "127.0.0.1")

#: Nom du nœud -> port TCP d'écoute.
PORTS: dict[str, int] = {
    "flight": 5001,
    "ai": 5002,
    "decision": 5003,
    "passenger": 5004,
}

#: Nœuds qui écoutent (destinations possibles d'un message).
NODES: tuple[str, ...] = tuple(PORTS)

#: Émetteurs possibles : les nœuds plus l'injecteur (qui n'écoute pas).
SOURCES: tuple[str, ...] = NODES + ("injector",)


def address(node: str) -> tuple[str, int]:
    """Adresse (hôte, port) d'un nœud."""
    return HOST, PORTS[node]


CONNECT_TIMEOUT_S: float = 1.0
SEND_RETRIES: int = 3
RETRY_BACKOFF_S: float = 0.05
MAX_MESSAGE_BYTES: int = 1 << 20  # 1 Mo, protège contre un préfixe corrompu

# --- Temps -----------------------------------------------------------------

#: 1 seconde réelle = 1 minute simulée.
SIM_MINUTES_PER_REAL_SECOND: float = 1.0

# --- Reproductibilité et fichiers -----------------------------------------

SEED: int = 42
ROOT: Path = Path(__file__).resolve().parent.parent
LOG_DIR: Path = Path(os.environ.get("SA_LOG_DIR", ROOT / "logs"))
SCENARIO_DIR: Path = ROOT / "scenarios"

# --- Données du scénario de référence (section 1 du guide) ----------------

#: Portes disponibles pour la réaffectation.
GATES: tuple[str, ...] = ("G12", "G15", "G18", "G20")

#: Position des portes le long du terminal (m). Distance = |a - b|.
#: Valeurs v0 : G15 est la plus proche de G03 parmi les portes de réaffectation
#: et au-delà de 600 m (donc bus), comme dans le scénario.
GATE_POSITIONS_M: dict[str, int] = {
    "G03": 0,
    "G12": 650,
    "G15": 700,
    "G18": 950,
    "G20": 1200,
}

N_BUSES: int = 5
BUS_DISTANCE_THRESHOLD_M: int = 600
WALK_SPEED_M_PER_MIN: float = 70.0
BUS_SPEED_M_PER_MIN: float = 300.0
SECURITY_RATE_PAX_PER_MIN: float = 10.0  # par file ouverte
SECURITY_ALERT_THRESHOLD: int = 100

#: Vols du scénario. slack_min = marge prévue entre l'arrivée et le vol suivant.
FLIGHTS: dict[str, dict] = {
    "TU701": {"pax_total": 240, "n_connecting": 35, "slack_min": 55,
              "gate": "G12", "connection_gate": "G03", "hour": 10},
    "TU702": {"pax_total": 180, "n_connecting": 12, "slack_min": 110,
              "gate": "G18", "connection_gate": "G03", "hour": 10},
    "TU703": {"pax_total": 200, "n_connecting": 20, "slack_min": 70,
              "gate": "G20", "connection_gate": "G03", "hour": 10},
}

#: Attente sécurité par défaut (min) tant qu'aucune mise à jour n'est reçue.
DEFAULT_SECURITY_WAIT_MIN: float = 5.0


def gate_distance(a: str, b: str) -> int:
    """Distance en mètres entre deux portes."""
    return abs(GATE_POSITIONS_M[a] - GATE_POSITIONS_M[b])

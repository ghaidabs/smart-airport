"""Politique de décision : transforme un risque (IA) en niveau et en actions.

Contrat (annexe B) :
    decide(flight, risk, context) -> {"level", "actions", "priority_hint"}

Règles (Guide P2, tâche 6) :
    risque >= 0,7           -> CRITIQUE  : porte libre la plus proche de la porte de
                               correspondance, bus si distance > 600 m, coupe-file
                               sécurité, notification
    0,4 <= risque < 0,7     -> SURVEILLE : notification + nouvelle prédiction à la
                               prochaine mise à jour
    risque < 0,4            -> AUCUNE    : rien

Cette fonction est PURE : elle ne réserve rien et n'envoie aucun message. Elle
décrit ce qu'il faut faire ; le nœud Décision (et le dispatcher de P5) l'exécute,
et la sûreté des ressources reste garantie par GateManager / BusPool (P4).
L'IA aide donc à décider, elle ne fournit aucune garantie temporelle.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

# Niveaux (ASCII, sans accent : ce sont des constantes de protocole)
CRITICAL = "CRITIQUE"
WATCH = "SURVEILLE"
NONE = "AUCUNE"

# Actions (vocabulaire partagé avec le nœud Décision)
RESERVE_GATE = "RESERVE_GATE_NEAREST"
BUSES = "BUSES"
FAST_TRACK = "SECURITY_FAST_TRACK"
NOTIFY = "NOTIFY"
REPREDICT = "REPREDICT_ON_UPDATE"

CRITICAL_THRESHOLD = 0.7
WATCH_THRESHOLD = 0.4
BUS_DISTANCE_M = 600.0


def level_for(risk: float) -> str:
    """Niveau associé à un risque (bornes inférieures incluses)."""
    if risk >= CRITICAL_THRESHOLD:
        return CRITICAL
    if risk >= WATCH_THRESHOLD:
        return WATCH
    return NONE


def decide(flight: str, risk: float, context: Mapping[str, Any] | None = None) -> dict:
    """Décide du niveau, des actions et de l'indication de priorité d'un vol.

    ``context`` (optionnel) : ``gate_distance_m`` = distance entre la porte
    d'arrivée envisagée et la porte de correspondance. Sans cette valeur, on ne
    déclenche pas de bus (le nœud Décision la complète après la réservation).

    ``priority_hint`` : plus il est grand, plus la situation est urgente ; c'est
    le risque lui-même, que le dispatcher de P5 utilise comme clé (bande 1).
    """
    if risk is None or not isinstance(risk, (int, float)) or math.isnan(risk) or not 0.0 <= risk <= 1.0:
        raise ValueError(f"risque invalide pour {flight} : {risk!r} (attendu dans [0, 1])")
    context = context or {}

    level = level_for(risk)
    actions: list[str] = []
    if level == CRITICAL:
        actions.append(RESERVE_GATE)
        distance = context.get("gate_distance_m")
        if distance is not None and distance > BUS_DISTANCE_M:
            actions.append(BUSES)
        actions += [FAST_TRACK, NOTIFY]
    elif level == WATCH:
        actions += [NOTIFY, REPREDICT]

    return {"level": level, "actions": actions, "priority_hint": float(risk)}
from __future__ import annotations

import math
from typing import Any, Mapping

CRITICAL = "CRITIQUE"
WATCH = "SURVEILLE"
NONE = "AUCUNE"

RESERVE_GATE = "RESERVE_GATE_NEAREST"
BUSES = "BUSES"
FAST_TRACK = "SECURITY_FAST_TRACK"
NOTIFY = "NOTIFY"
REPREDICT = "REPREDICT_ON_UPDATE"

CRITICAL_THRESHOLD = 0.7
WATCH_THRESHOLD = 0.4
BUS_DISTANCE_M = 600.0


def level_for(risk: float) -> str:
    if risk >= CRITICAL_THRESHOLD:
        return CRITICAL
    if risk >= WATCH_THRESHOLD:
        return WATCH
    return NONE


def decide(flight: str, risk: float, context: Mapping[str, Any] | None = None) -> dict:
    
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
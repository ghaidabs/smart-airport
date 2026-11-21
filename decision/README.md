# decision/

**Responsable : P2** (`policy.py`). Le nœud Décision lui-même est dans `nodes/decision_node.py` (P1), le dispatcher dans `sched/` (P5).

`policy.decide(flight, risk, context) -> {"level", "actions", "priority_hint"}` — fonction pure.

| Risque | Niveau | Actions |
|---|---|---|
| >= 0,7 | `CRITIQUE` | `RESERVE_GATE_NEAREST`, `BUSES` (si `context["gate_distance_m"]` > 600), `SECURITY_FAST_TRACK`, `NOTIFY` |
| [0,4 ; 0,7[ | `SURVEILLE` | `NOTIFY`, `REPREDICT_ON_UPDATE` |
| < 0,4 | `AUCUNE` | aucune |

`priority_hint` = le risque (plus grand = plus urgent) : clé de la bande 1 du dispatcher de P5.
La fonction ne réserve rien : P4 (`GateManager`, `BusPool`) garantit l'exclusion mutuelle.
Les seuils sont justifiés par la courbe précision-rappel (`experiments/results/ai_precision_recall.png`).
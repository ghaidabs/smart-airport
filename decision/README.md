# decision/

**Responsable : P2** (`policy.py`). Le nœud Décision est dans `nodes/decision_node.py` (P1) ; il appelle `decide()` (P2). Ses messages sont traités en FIFO et mesurés comme jobs de T4, en attendant le dispatcher de `sched/` (P5).

`policy.decide(flight, risk, context) -> {"level", "actions", "priority_hint"}` — fonction pure.

| Risque | Niveau | Actions |
|---|---|---|
| >= 0,7 | `CRITIQUE` | `reserve_gate`, `bus` (si `context["gate_distance_m"]` > 600), `fast_track`, `notify` |
| [0,4 ; 0,7[ | `SURVEILLE` | `notify`, `re_predict` |
| < 0,4 | `AUCUNE` | aucune |

`priority_hint` = le risque (plus grand = plus urgent) : clé de la bande 1 du dispatcher de P5.
La fonction ne réserve rien : P4 (`GateManager`, `BusPool`) garantit l'exclusion mutuelle.
Les seuils sont justifiés par la courbe précision-rappel (`experiments/results/ai_precision_recall.png`).

Vocabulaire d'actions partagé avec `nodes/decision_node.py` et `nodes/passenger_node.py` (P1) : le nœud Passagers teste `"bus" in actions`.
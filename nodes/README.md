# nodes/

**Squelettes : P1.** Les tâches temps réel sont déclarées dans `setup()` via `self.rt` ([`rt/runtime.py`](../rt/runtime.py)).

| Fichier | Port | Branché | Reste à brancher |
| --- | --- | --- | --- |
| `flight_node.py` | 5001 | T1 (P3), `FlightTable` | `GateManager` (P4) |
| `ai_node.py` | 5002 | T3, `RiskPredictor` (P2) | — |
| `decision_node.py` | 5003 | `policy.decide` (P2), mesure de T4 | Dispatcher (P5) |
| `passenger_node.py` | 5004 | T2, T5 (P3) | `BusPool` (P4) |

Le port d'un nœud ne s'ouvre qu'après `setup()` : quand `run_all` annonce les nœuds prêts, le modèle IA est chargé et les tâches tournent.

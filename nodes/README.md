# nodes/

**Squelettes : P1.** Chaque partie remplace ensuite les mocks par sa logique.

| Fichier | Port | Logique métier |
| --- | --- | --- |
| `flight_node.py` | 5001 | P1, P3 (T1), P4 (`GateManager`) |
| `ai_node.py` | 5002 | P2 (T3) |
| `decision_node.py` | 5003 | P5 (dispatcher), P2 (politique) |
| `passenger_node.py` | 5004 | P4, P3 (T2, T5) |

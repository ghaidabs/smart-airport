# sync/

**Responsable : P4.** Synchronisation et ressources partagées.

Fichiers prévus :

- `gate_manager.py` : moniteur `GateManager` (sémaphore compteur et mutex).
- `bus_pool.py` : pool de 5 bus, allocation « tout ou rien ».
- `event_buffer.py` : `EventBuffer`, tampon borné producteur-consommateur.
- `race_demo.py` : démonstration de la race condition en Python.
- `race_demo.c` et `Makefile` : même démonstration en C POSIX.

Référence : annexe B du guide.

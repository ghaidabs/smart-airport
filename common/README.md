# common/

**Responsable : P1.** Socle de communication partagé par tous les nœuds.

Fichiers prévus :

- `protocol.py` : classe `Message`, sérialisation JSON avec préfixe de longueur sur 4 octets.
- `net.py` : `NodeServer` et `send(dest, msg)` sur TCP.
- `config.py` : ports 5001 à 5004 et échelle de temps.
- `logger.py` : journal CSV `ts_ns, node, thread, corr_id, event, detail`.

Référence : annexe A du guide.

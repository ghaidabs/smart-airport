# AI Smart Airport — Projet STR

Système temps réel distribué et simulé : **l'IA prédit, le système temps réel garantit.**
Quand un vol arrive en retard, le système prédit le risque de correspondance manquée et
réaffecte portes et bus avant des deadlines garanties.

## Lancement

Linux (Ubuntu) ou WSL2, Python 3.11 ou plus.

```bash
pip install -r requirements.txt
./run_all.sh                         # terminal 1 : lance les 4 nœuds (Ctrl+C pour arrêter)
python3 -m scenarios.inject tu701    # terminal 2 : rejoue le scénario de référence
python3 -m tools.timeline            # chronologie et temps de bout en bout
```

Commandes de l'injecteur : `tu701`, `demo_final`, `overload_x2`, `play <fichier.json>`,
`delay TU701 25`, `gate_down G12`, `queue 150`, options `--load x2`, `--rate N`, `--fast`.

Tests : `python3 -m pytest tests/` (dont `test_e2e_tu701.py`, qui lance les 4 nœuds).

## Architecture

| Nœud | Port | Rôle |
| --- | --- | --- |
| `flight` | 5001 | Vols et ressources (portes) — T1 FlightMonitor |
| `ai` | 5002 | Inférence du risque — T3 AIInference |
| `decision` | 5003 | Politique et dispatcher — T4 DecisionResource |
| `passenger` | 5004 | File de sécurité et bus — T2, T5 |

Les nœuds sont des processus séparés qui échangent des messages JSON sur TCP, avec un
préfixe de longueur de 4 octets (annexe A du guide, `common/protocol.py`). Chaque nœud
écrit `logs/<nœud>.csv` (`ts_ns, node, thread, corr_id, event, detail`), et
`tools/timeline.py` fusionne ces journaux.

Chaîne du scénario TU701 :

```
injector --FLIGHT_DELAY--> flight --RISK_REQUEST--> ai --RISK_RESULT--> decision
decision --GATE_REQUEST--> flight --GATE_ASSIGNED--> decision
decision --PAX_ACTION--> passenger --PAX_DONE--> decision --DECISION--> tous
injector --QUEUE_UPDATE--> passenger --SECURITY_ALERT--> decision
```

`corr_id` (identifiant de l'événement initial) et `payload.t0_ns` (son horodatage) sont
recopiés dans toute la chaîne par `Message.derive`, ce qui donne `latency_ms` dans le
message `DECISION`.

## État actuel : mocks

Les nœuds ont un comportement factice, que chaque partie remplace par son code :
risques du scénario dans le nœud IA (P2), première porte libre (P4), seuils fixes
(P2/P5), traitement sur un seul thread (P3/P5). Voir le README de chaque dossier.

## Organisation

Une branche par partie (`p1-net`, `p2-ai`, `p3-rt`...), fusion dans `main` par pull
request relue ; `main` toujours exécutable. Graine aléatoire 42 partout.

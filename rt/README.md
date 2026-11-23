# rt/

**Responsable : P3.** Modélisation temps réel, threads et mesures (milestone M3).

Ce paquet transforme le système en tâches mesurables : il fournit les threads périodiques et sporadiques utilisés par les nœuds, les corps de T1, T2 et T5, et la table officielle des tâches.

## Contenu

| Fichier | Rôle |
| --- | --- |
| `tasks.py` | Table des tâches (`TaskSpec`), avec les C mesurés qui remplacent la v0 |
| `measured_wcet.json` | C mesurés de T1, T2 et T5, produits par `experiments/m3_wcet.py` |
| `periodic.py` | `PeriodicTask`, avec dates de réveil absolues et politique de dépassement |
| `sporadic.py` | `SporadicTask`, avec intervalle minimal entre arrivées |
| `metrics.py` | Collecte CSV et résumé (Cmin, Cmoy, Cmax, Rmax, dépassements, gigue max) |
| `monitors.py` | Corps de T1 `FlightMonitor`, T2 `QueueMonitor` et T5 `SecurityAlert` |
| `system.py` | Épinglage sur un cœur et tentative de `SCHED_FIFO` |
| `real_gantt.py` | Gantt réel construit à partir des jobs mesurés |
| `periodic_c/` | Bonus : la même tâche périodique en C POSIX |

## Lancement

Sous Linux ou WSL2, depuis la racine du dépôt :

```bash
sudo apt install python3-venv         # une seule fois, si venv est absent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pytest tests/test_rt.py     # tests du cadre
python -m rt.tasks                    # table courante et U
bash experiments/run_m3.sh            # toutes les mesures, environ 3 min 30
```

**`run_m3.sh` écrase `rt/measured_wcet.json` et les résultats M3 de `experiments/results/`.** Les C utilisés par `load_task_table()` changent donc à chaque exécution. Les valeurs de ce README restent celles de l'exécution de référence décrite ci-dessous, jusqu'à leur mise à jour manuelle.

## Utilisation dans un nœud

```python
from rt.metrics import MetricsCollector
from rt.monitors import FlightMonitor, FlightTable, reference_flights
from rt.periodic import PeriodicTask
from rt.sporadic import SporadicTask
from rt.tasks import get_task

metrics = MetricsCollector()
table = FlightTable(reference_flights())
t1 = get_task("T1")
PeriodicTask("T1", t1.period_ms, t1.deadline_ms, FlightMonitor(table, send),
             metrics=metrics).start()

t3 = get_task("T3")
SporadicTask("T3", t3.period_ms, t3.deadline_ms, handle_request, event_buffer,
             metrics=metrics).start()
```

`send(dest, msg)` et `event_buffer` sont fournis par P1 et P4. `SporadicTask` attend de `event_buffer.get(timeout)` un événement, ou `None` à l'expiration. `QueueEventSource` remplace `EventBuffer` tant que P4 n'est pas branché.

## Choix de conception

- **Réveils absolus.** Le réveil `k` vaut `origine + k·P`, donc la durée des jobs ne décale jamais les réveils suivants.
- **Dépassement : sauter (`skip`).** Si un job finit après le réveil suivant, les réveils passés sont sautés et comptés. Une surveillance n'a besoin que de l'état le plus récent, et rattraper ajouterait du retard. `catch_up` reste disponible.
- **Sporadiques.** Un événement trop rapproché attend en file. R et la deadline partent de son horodatage `ts_ns`, donc cette attente compte dans la mesure.
- **Mesure de C.** `c_ms` est la durée écoulée et inclut les préemptions. `cpu_ns` compte seulement le temps CPU du thread.

## Conditions de mesure

Toutes les valeurs mesurées de ce README et de [`periodic_c/README.md`](periodic_c/README.md) viennent d'**une seule exécution de référence** : `bash experiments/run_m3.sh`, le 23/11/2026 entre 09:15 et 09:18.

| Élément | Configuration de référence |
| --- | --- |
| Processeur | AMD Ryzen 7 5800H, 16 processeurs logiques visibles dans WSL2 |
| Mémoire | 7,5 Gio visibles dans WSL2 |
| Système | WSL2, noyau Linux `6.6.87.2-microsoft-standard-WSL2`, horloge `tsc` |
| Logiciels | CPython 3.14.4 avec GIL (intervalle de commutation 5 ms), GCC 15.2.0 (`-O2`), NumPy 2.5.5, Matplotlib 3.11.2 |
| Ordonnancement | Processus épinglé sur le processeur logique 0, politique Linux par défaut, sans `SCHED_FIFO` |
| WCET | 1000 exécutions seules par tâche, chemin le plus coûteux, C = max × 1,2 |
| Charge | 60 s en charge normale, puis 60 s en charge × 2, graine 42 |
| Réseau et mocks | Envoi réseau remplacé par des tampons en mémoire, T3 et T4 simulés avec leur C v0 |
| Non contrôlé | Activité de Windows et des autres programmes, fréquence du processeur, placement du cœur virtuel par l'hyperviseur |

**Ce qui est reproductible :** la graine 42 fixe le scénario injecté (160 événements en charge normale, 312 en charge × 2). Les périodes, deadlines et C v0 sont fixes.

**Ce qui varie :** les durées, les réponses et la gigue dépendent de l'état de la machine. Les maximums sont les plus sensibles, car un seul job retardé suffit à les changer. Comparaison avec une exécution précédente sur la même machine :

| Grandeur | Exécution précédente | Référence |
| --- | ---: | ---: |
| C de T1 (ms) | 0,127 | 0,4685 |
| C de T2 (ms) | 0,062 | 0,335 |
| C de T5 (ms) | 0,024 | 0,0436 |
| U | 0,4016 | 0,4058 |
| Rmax de T1, charge normale (ms) | 3,347 | 10,811 |
| Dépassements de T3, charge × 2 | 15 | 16 |

U reste stable car il est dominé par les C v0 de T3 et T4 (0,40 sur 0,4058). Il faut donc interpréter ces mesures comme **les valeurs d'une exécution dans ces conditions**, pas comme des constantes du système. Après une nouvelle exécution retenue, mettre à jour ensemble cette section, les tableaux et leur analyse.

## Table des tâches finale

Valeurs de l'exécution de référence (`rt/measured_wcet.json`).

| Tâche | Nœud | Type | C (ms) | P ou min. (ms) | D (ms) | Criticité | Source de C |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| T1 FlightMonitor | Vols | Périodique | 0,4685 | 100 | 100 | Haute | Mesuré |
| T2 QueueMonitor | Passagers | Périodique | 0,335 | 500 | 500 | Moyenne | Mesuré |
| T3 AIInference | IA | Sporadique | 20 | min. 100 | 200 | Moyenne | v0, attendu de P2 |
| T4 DecisionResource | Décision | Sporadique | 10 | min. 50 | 100 | Haute | v0, attendu de P5 |
| T5 SecurityAlert | Passagers | Sporadique | 0,0436 | min. 100 | 50 | Critique | Mesuré |
| T6 HistoryRetrain | Cloud | Apériodique | quelques s | — | aucune | Basse | Hors analyse |

U = Σ C/P = **0,4058**. C vaut le maximum de 1000 exécutions seules × 1,2, sur le chemin le plus coûteux et sans envoi réseau.

## Résultats M3

Exécution de référence, dans les [conditions de mesure](#conditions-de-mesure) ci-dessus. Valeurs en ms.

| Tâche | Charge | Jobs | Dépassements | Cmoy | Cmax | Rmax | Gigue max |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| T1 | normale | 601 | 0 | 0,087 | 0,317 | 10,811 | 10,759 |
| T1 | × 2 | 601 | 0 | 0,101 | 0,665 | 1,425 | 1,286 |
| T2 | normale | 121 | 0 | 0,040 | 0,192 | 10,506 | 10,478 |
| T2 | × 2 | 121 | 0 | 0,037 | 0,175 | 8,426 | 8,404 |
| T3 (mock) | normale | 130 | **1** | 20,315 | 23,088 | 227,186 | 206,715 |
| T3 (mock) | × 2 | 246 | **16** | 20,295 | 21,777 | 334,213 | 313,939 |
| T4 (mock) | normale | 130 | 0 | 10,046 | 10,399 | 10,538 | 0,271 |
| T4 (mock) | × 2 | 246 | 0 | 10,048 | 11,266 | 11,373 | 0,549 |
| T5 | normale | 8 | 0 | 0,026 | 0,035 | 0,192 | 0,157 |
| T5 | × 2 | 14 | 0 | 0,023 | 0,036 | 8,187 | 8,157 |

Lecture des résultats :

- **T1, T2, T4 et T5 respectent toutes leurs deadlines**, y compris à charge double.
- **Les dépassements de T3 viennent de la file, pas de son calcul.** Le générateur envoie des rafales plus rapprochées que l'intervalle minimal de 100 ms, ce qui sort de l'hypothèse sporadique. Les jobs en retard ont attendu au moins 204 ms avant de démarrer, pour un C d'au plus 21 ms.
- **Un Rmax plus faible à charge × 2 ne signifie pas que la charge améliore le système.** Pour T1, 10,811 ms en charge normale contre 1,425 ms à charge × 2 reflète surtout un pic ponctuel de latence.
- **Le WCET isolé n'est pas une borne garantie.** À charge × 2, le temps CPU d'un job de T1 atteint 0,655 ms, au-dessus de son C de 0,4685 ms. T2 et T5 restent sous leur C dans cette exécution, mais ce n'était pas le cas lors de l'exécution précédente.
- **Le Gantt TU701 est mis en scène** : TU703 et la panne G12 sont injectés 16 ms avant le tick de T1. Il montre T1, T3 et T4 entrelacés sur un seul cœur, pas exécutés en parallèle.

Fichiers dans `experiments/results/` : `m3_task_table.csv`, `m3_jobs_*.csv`, `m3_summary_*.csv`, `m3_response_times.png`, `m3_gantt_tu701.png`, `m3_jitter_c_vs_python.csv` et `.png`.

## Limites

- **Temps mesurés, pas garantis.** Linux et WSL2 ne sont pas des OS temps réel durs.
- **GIL.** Un seul thread Python exécute du bytecode à la fois, et le changement de thread se fait par défaut toutes les 5 ms. C'est compatible avec les gigues de plusieurs ms observées (jusqu'à 10,8 ms pour T1), mais ce n'est pas la seule cause possible : la version C, sans GIL, a aussi connu un pic de 13,4 ms sous WSL2.
- **Pas de priorités réelles.** `SCHED_FIFO` n'a pas pu être activé sans sudo. Dans un processus Python, il ne donnerait de toute façon pas de priorité entre threads à cause du GIL.
- **Mocks.** T3 et T4 consomment leur C v0 et le réseau est remplacé par des tampons en mémoire. Il faudra remesurer après l'intégration de P1, P2, P4 et P5.

## Points d'intégration

- **P1 :** passer `common.net.send` et, via `message_factory`, la classe `Message` aux moniteurs.
- **P1 et P2 :** `QUEUE_UPDATE` ne va qu'au nœud Passagers (annexe A), alors que T1, sur le nœud Vols, doit fournir `security_wait_min` au modèle. Il faut un message qui appelle `FlightTable.set_security_wait`, sinon cette variable reste à 0.
- **P2 :** transmettre le C de T3 (max d'inférence × 1,2).
- **P5 :** lire la table avec `rt.tasks.load_task_table()`. Un paquet `sched/` avec `__init__.py` masquerait le module standard `sched` de Python quand la racine du dépôt est dans le chemin d'import.

Référence : annexe B du guide et table des tâches v0 (section 4).

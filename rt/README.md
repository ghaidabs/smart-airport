# rt/

**Responsable : P3.** Modélisation temps réel, threads et mesures (milestone M3).

Ce paquet transforme le système en tâches mesurables : il fournit les threads périodiques et sporadiques utilisés par les nœuds, les corps de T1, T2 et T5, et la table officielle des tâches.

## Contenu

| Fichier | Rôle |
| --- | --- |
| `tasks.py` | Table des tâches (`TaskSpec`), avec les C mesurés qui remplacent la v0 |
| `measured_wcet.json` | C mesurés de T1, T2, T3 et T5, produits par `experiments/m3_wcet.py` |
| `periodic.py` | `PeriodicTask`, avec dates de réveil absolues et politique de dépassement |
| `sporadic.py` | `SporadicTask`, avec intervalle minimal entre arrivées |
| `metrics.py` | Collecte CSV et résumé (Cmin, Cmoy, Cmax, Rmax, dépassements, gigue max) |
| `monitors.py` | Corps de T1 `FlightMonitor`, T2 `QueueMonitor`, T5 `SecurityAlert`, état `FlightTable` |
| `runtime.py` | `NodeRuntime` : crée les tâches d'un nœud, mesure ses jobs, écrit `logs/rt/` |
| `report.py` | Résumé des mesures du système réel (`python -m rt.report`) |
| `system.py` | Épinglage sur un cœur et tentative de `SCHED_FIFO` |
| `real_gantt.py` | Gantt réel construit à partir des jobs mesurés |

## Lancement

Sous Linux ou WSL2, depuis la racine du dépôt :

```bash
sudo apt install python3-venv         # une seule fois, si venv est absent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pytest tests/test_rt.py     # tests du cadre
python -m rt.tasks                    # table courante et U
bash experiments/run_m3.sh            # toutes les mesures, environ 3 min
python -m rt.report                   # mesures du système réel, après arrêt des nœuds
```

**`run_m3.sh` écrase `rt/measured_wcet.json` et les résultats M3 de `experiments/results/`.** Les C utilisés par `load_task_table()` changent donc à chaque exécution. Les valeurs de ce README restent celles de l'exécution de référence décrite ci-dessous, jusqu'à leur mise à jour manuelle.

## Branchement dans les nœuds

| Tâche | Nœud | Activation | Corps |
| --- | --- | --- | --- |
| T1 | `flight` | Toutes les 100 ms | `FlightMonitor` lit `FlightTable` et envoie `RISK_REQUEST` |
| T2 | `passenger` | Toutes les 500 ms | `QueueMonitor` simule la file, active T5, publie l'attente si elle varie d'au moins 2 min |
| T3 | `ai` | `RISK_REQUEST` | `AINode.handle` prédit le lot et envoie un `RISK_RESULT` par vol |
| T4 | `decision` | `RISK_RESULT`, `GATE_ASSIGNED`, `PAX_DONE`, `SECURITY_ALERT` | Handlers du nœud, FIFO mesurée en attendant le dispatcher de P5 |
| T5 | `passenger` | Alerte de T2 ou `QUEUE_UPDATE` | `SecurityAlert` envoie `SECURITY_ALERT` |

Une tâche se déclare dans `setup()`. `BaseNode.run` la démarre, puis l'arrête et écrit ses mesures :

```python
class FlightNode(BaseNode):
    name = "flight"

    def setup(self):
        self.table = FlightTable(reference_flights())
        self.rt.periodic("T1", FlightMonitor(self.table, self.send, net_message))
```

`self.rt.sporadic("T3", corps)` renvoie le tampon à alimenter depuis le handler du message.

## Choix de conception

- **Réveils absolus.** Le réveil `k` vaut `origine + k·P`, donc la durée des jobs ne décale jamais les réveils suivants.
- **Dépassement : sauter (`skip`).** Si un job finit après le réveil suivant, les réveils passés sont sautés et comptés. Une surveillance n'a besoin que de l'état le plus récent. `catch_up` reste disponible.
- **Sporadiques.** Un événement trop rapproché attend en file. R et la deadline partent de son horodatage, donc cette attente compte dans la mesure.
- **Regroupement dans T1.** Un réveil envoie au plus un `RISK_REQUEST`, avec tous les vols. T3 reçoit ainsi au plus une requête par intervalle minimal de 100 ms. Le `corr_id` et le `t0_ns` sont ceux de l'événement le plus ancien, donc la latence affichée est la plus défavorable.
- **Pas de boucle de prédiction.** Une porte subie (`set_gate`) déclenche T1. Une porte choisie par le système (`assign_gate`) ne le déclenche pas.
- **Alerte immédiate.** `QUEUE_UPDATE` évalue l'alerte sans attendre T2, car T2 a une période de 500 ms et T5 une deadline de 50 ms. T2 ne renvoie pas une alerte déjà émise.
- **T4 sans intervalle imposé.** Une requête produit plusieurs `RISK_RESULT`. Les espacer de 50 ms ferait manquer la deadline à cause du découpage en messages.
- **Mesure de C.** `c_ms` est la durée écoulée et inclut les préemptions. `cpu_ns` compte seulement le temps CPU du thread.

## Conditions de mesure

Les valeurs M3 de ce README viennent d'**une seule exécution de référence** de `bash experiments/run_m3.sh`, le 23/11/2026, après le branchement de P1 et P2.

| Élément | Configuration de référence |
| --- | --- |
| Processeur | AMD Ryzen 7 5800H, 16 processeurs logiques visibles dans WSL2 |
| Mémoire | 7,5 Gio visibles dans WSL2 |
| Système | WSL2, noyau Linux `6.6.87.2-microsoft-standard-WSL2`, horloge `tsc` |
| Logiciels | CPython 3.14.4 avec GIL, NumPy 2.5.5, Matplotlib 3.11.2, scikit-learn 1.9.1 |
| Ordonnancement | Processus épinglé sur le processeur logique 0, politique Linux par défaut, sans `SCHED_FIFO` |
| WCET | 1000 exécutions seules par tâche, chemin le plus coûteux, C = max × 1,2 |
| Charge M3 | 60 s en charge normale, puis 60 s en charge × 2, graine 42 |
| T3 et T4 | T3 exécute le vrai modèle sur 23 vols. T4 reste un mock à son C v0 |
| Réseau | Remplacé par des tampons en mémoire dans M3, réel dans la mesure du système complet |
| Non contrôlé | Activité de Windows et des autres programmes, fréquence du processeur, placement du cœur virtuel |

La graine fixe le scénario injecté, pas les temps mesurés. Les maximums sont les plus sensibles, car un seul job retardé suffit à les changer.

## Table des tâches finale

Valeurs de `rt/measured_wcet.json`.

| Tâche | Nœud | Type | C (ms) | P ou min. (ms) | D (ms) | Criticité | Source de C |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| T1 FlightMonitor | Vols | Périodique | 0,0754 | 100 | 100 | Haute | Mesuré |
| T2 QueueMonitor | Passagers | Périodique | 0,0365 | 500 | 500 | Moyenne | Mesuré |
| T3 AIInference | IA | Sporadique | 0,9692 | min. 100 | 200 | Moyenne | Mesuré, vrai modèle, 23 vols |
| T4 DecisionResource | Décision | Sporadique | 10 | min. 50 | 100 | Haute | v0, attendu de P5 |
| T5 SecurityAlert | Passagers | Sporadique | 0,0578 | min. 100 | 50 | Critique | Mesuré |
| T6 HistoryRetrain | Cloud | Apériodique | quelques s | — | aucune | Basse | Hors analyse |

U = Σ C/P = **0,2111**, dont 0,20 pour le C v0 de T4.

**Pour l'analyse de P5 :** ces C isolés excluent le réseau et sont dépassés dans le système complet. Avec les Cmax observés sous stress (T1 2,206 ; T2 0,474 ; T3 4,037 ; T5 0,192 ms) et T4 à 10 ms, U ≈ **0,265**. Cette valeur est plus prudente, mais reste une mesure et non une borne garantie.

## Résultats M3

Exécution de référence, sans réseau. Valeurs en ms.

| Tâche | Charge | Jobs | Dépassements | Cmoy | Cmax | Rmax | Gigue max |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| T1 | normale | 601 | 0 | 0,038 | 0,223 | 0,971 | 0,878 |
| T1 | × 2 | 601 | 0 | 0,072 | 0,336 | 12,632 | 12,584 |
| T2 | normale | 121 | 0 | 0,023 | 0,088 | 1,386 | 1,375 |
| T2 | × 2 | 121 | 0 | 0,034 | 0,190 | 1,752 | 1,741 |
| T3 | normale | 116 | 0 | 1,243 | 1,746 | 2,038 | 0,914 |
| T3 | × 2 | 203 | 0 | 1,301 | 1,784 | 3,081 | 1,796 |
| T4 (mock) | normale | 116 | 0 | 10,026 | 10,741 | 10,825 | 0,204 |
| T4 (mock) | × 2 | 203 | 0 | 10,024 | 10,160 | 10,203 | 0,137 |
| T5 | normale | 8 | 0 | 0,021 | 0,073 | 0,123 | 0,051 |
| T5 | × 2 | 14 | 0 | 0,018 | 0,043 | 0,239 | 0,224 |

- **Aucune deadline manquée**, y compris à charge double. Les anciens dépassements de T3 venaient du mock à 20 ms et des requêtes trop rapprochées, que T1 regroupe désormais.
- **Le WCET isolé n'est pas une borne garantie.** Sous charge, le temps CPU de T1, T2 et T3 dépasse leur C isolé (T3 : 1,782 ms contre 0,969 ms).
- **Le Gantt TU701 est mis en scène**, avec T3 et T4 à leur C v0, pour rendre visible leur entrelacement avec T1 sur un seul cœur.

Fichiers dans `experiments/results/` : `m3_task_table.csv`, `m3_jobs_*.csv`, `m3_summary_*.csv`, `m3_response_times.png` et `m3_gantt_tu701.png`.

## Mesure du système complet

4 processus, TCP réel, vrai modèle, T4 en FIFO. Essai de stress : 600 événements à 10 par seconde, soit 13 080 décisions, sans dépassement, erreur ni message perdu. Valeurs en ms.

| Tâche | Jobs | Cmoy | Cmax | Rmax | D |
| --- | ---: | ---: | ---: | ---: | ---: |
| T1 | 731 | 0,337 | 2,206 | 2,385 | 100 |
| T2 | 147 | 0,026 | 0,474 | 0,601 | 500 |
| T3 | 590 | 2,221 | 4,037 | 23,622 | 200 |
| T4 | 17 528 | 0,269 | 2,370 | 10,818 | 100 |
| T5 | 30 | 0,130 | 0,192 | 1,016 | 50 |

Temps de bout en bout, de l'événement à la décision : médiane 62,6 ms, p99 79,9 ms, maximum 110,1 ms. Il inclut l'attente du réveil de T1, jusqu'à 100 ms. Une alerte de sécurité ne passe pas par T1 et arrive en environ 3 ms.

## Priorités SCHED_FIFO (optionnelles)

Priorités deadline monotonic : T5 (80) > T1 (70) > T4 (60) > T3 (50) > T2 (40). Elles sont désactivées par défaut et n'ont pas servi à l'exécution de référence.

```bash
sudo prlimit --pid $$ --rtprio=80:80   # autorise ce terminal, sans lancer Python en root
python -m experiments.m3_load --fifo --out-dir experiments/results/fifo
RT_FIFO=1 ./run_all.sh                 # système complet
```

`--fifo` s'arrête si les priorités sont refusées. Avec `RT_FIFO=1`, un refus est signalé dans `logs/<nœud>.out` et la tâche continue en politique normale. Le GIL ignore ces priorités : l'intervalle de bascule passe de 5 ms à 0,5 ms pour réduire l'attente d'une tâche prioritaire, sans la supprimer.

## Limites

- **Temps mesurés, pas garantis.** Linux et WSL2 ne sont pas des OS temps réel durs.
- **GIL.** Un seul thread Python d'un processus exécute du bytecode à la fois. Les 4 nœuds étant des processus séparés, ils ne partagent toutefois pas le même GIL.
- **Priorités partielles.** Avec `RT_FIFO=1`, seules les tâches T1 à T5 sont prioritaires, pas les threads réseau. Les nœuds ne sont pas épinglés sur un seul cœur dans le système complet.
- **T4 n'est pas encore ordonnancée.** Elle traite les messages dans leur ordre d'arrivée jusqu'au dispatcher de P5.
- **Windows.** `run_all.ps1` force l'arrêt des nœuds, qui n'écrivent donc pas `logs/rt/`.

## Points d'intégration restants

- **P4 :** remplacer `QueueEventSource` et la `queue.Queue` des nœuds par `EventBuffer`, les portes de `flight_node.py` par `GateManager`, les bus de `passenger_node.py` par `BusPool`.
- **P5 :** remplacer `DecisionNode.dispatch` par le dispatcher, fournir le C de T4 et lire la table avec `rt.tasks.load_task_table()`. Un paquet `sched/` avec `__init__.py` masquerait le module standard `sched` de Python.

Référence : annexe B du guide et table des tâches v0 (section 4).

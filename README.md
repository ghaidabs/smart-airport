# AI Smart Airport — Projet STR

Système temps réel distribué et simulé : **l'IA prédit, le système temps réel garantit.**
Quand un vol arrive en retard, le système prédit le risque de correspondance manquée et
réaffecte portes et bus avant des deadlines garanties.

Sommaire : [Installation](#1-installation) · [Lancer le système](#2-lancer-le-système) ·
[Injecter des événements](#3-injecter-des-événements) ·
[Lire la chronologie](#4-lire-la-chronologie) · [Tests](#5-tests) ·
[Architecture](#6-architecture) · [Brancher sa partie](#7-brancher-sa-partie) ·
[Dépannage](#8-dépannage)

---

## 1. Installation

Prérequis : **Python 3.11 ou plus**, sous **Linux (Ubuntu) ou WSL2** pour la démo et les
mesures. Windows natif suffit pour développer (voir [section 2](#sous-windows-développement)).

```bash
git clone <url-du-dépôt> smart-airport
cd smart-airport
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Lancer le système

Il faut **deux terminaux** : un pour les 4 nœuds, un pour injecter les événements.

### Sous Linux / WSL (démo et mesures)

Terminal 1 :

```console
$ ./run_all.sh
nœuds prêts
4 nœuds prêts (ports 5001-5004). Ctrl+C pour arrêter.
Sorties console : logs/<nœud>.out ; journaux : logs/<nœud>.csv
```

`run_all.sh` vide `logs/*.csv` à chaque lancement (`./run_all.sh --keep-logs` pour les
garder). **Ctrl+C** arrête les 4 nœuds.

### Sous Windows (développement)

Terminal 1 (PowerShell) :

```console
PS> powershell -ExecutionPolicy Bypass -File .\run_all.ps1
nœuds prêts
4 nœuds prêts (ports 5001-5004). Ctrl+C pour arrêter.
```

Options : `-KeepLogs`, `-Python py`. Dans les commandes suivantes, remplacer `python3` par
`python`.

> L'horloge de Windows avance par pas d'environ 0,5 ms : les latences mesurées y sont
> imprécises. Faire la démo et les mesures M7 sous Linux/WSL.
>
> `run_all.ps1` doit rester enregistré en **UTF-8 avec BOM** : sans BOM, Windows
> PowerShell 5.1 lit mal les accents et le script ne se charge plus.

## 3. Injecter des événements

Terminal 2, depuis la racine du dépôt, nœuds lancés.

### Le scénario de référence

```console
$ python3 -m scenarios.inject tu701
[10:05] delay TU701 20 -> flight corr_id=evt-e909-0001 : envoyé
[10:06] gate_down G12 -> flight corr_id=evt-e909-0002 : envoyé
[10:07] queue 120 -> passenger corr_id=evt-e909-0003 : envoyé
3/3 événement(s) envoyé(s)
```

Les événements respectent l'heure simulée : **1 s réelle = 1 min simulée**, donc 10:05 →
10:07 prend 2 s. Chaque événement reçoit un `corr_id`, qui sert ensuite à le suivre
dans la chronologie.

### Toutes les commandes

| Commande | Effet |
| --- | --- |
| `python3 -m scenarios.inject tu701` | Scénario de référence : TU701 +20 min, G12 indisponible, file à 120 |
| `python3 -m scenarios.inject demo_final` | Variante de la démo : +25 min, G12 indisponible, file à 150 |
| `python3 -m scenarios.inject overload_x2` | Démo finale avec deux fois plus d'événements |
| `python3 -m scenarios.inject play mon_scenario.json` | Rejoue un fichier de scénario |
| `python3 -m scenarios.inject delay TU701 25` | Un vol annonce un retard (min) |
| `python3 -m scenarios.inject gate_down G12` | Une porte devient indisponible |
| `python3 -m scenarios.inject queue 150` | Nouvelle longueur de la file de sécurité |

| Option | Effet |
| --- | --- |
| `--load x2` | Multiplie les événements : chaque `delay` est dupliqué sur un vol synthétique (TU801, TU802...), chaque `queue` est rejoué ; `gate_down` n'est pas dupliqué |
| `--rate 50` | Envoie à 50 messages par seconde (ignore l'heure simulée) |
| `--fast` | Envoie tout sans attendre |

Exemples :

```console
$ python3 -m scenarios.inject tu701 --load x2 --fast
$ python3 -m scenarios.inject queue 80        # sous le seuil de 100 : pas d'alerte
$ python3 -m scenarios.inject delay TU799 30  # vol inconnu : créé comme vol synthétique
```

Format d'un fichier de scénario (`scenarios/tu701.json`) :

```json
{
  "name": "tu701",
  "load": 1,
  "events": [
    {"at": "10:05", "cmd": "delay", "args": ["TU701", 20]},
    {"at": "10:06", "cmd": "gate_down", "args": ["G12"]},
    {"at": "10:07", "cmd": "queue", "args": [120]}
  ]
}
```

## 4. Lire la chronologie

`tools/timeline.py` fusionne les journaux des nœuds (`logs/*.csv`).

### Résumé : temps de bout en bout

```console
$ python3 -m tools.timeline --summary

== evt-e909-0002  (GATE_UNAVAILABLE injector->flight)
   bout en bout : 1re décision 2.421 ms, dernière 2.978 ms (3 décision(s))
   réseau (aller simple) :
     ai->decision           moy    0.186 ms   max    0.559 ms   n=3
     flight->ai             moy    0.523 ms   max    0.523 ms   n=1
     ...
   traitement par nœud :
     ai                     moy    1.339 ms   max    1.339 ms   n=1
     ...
```

- **bout en bout** : de l'envoi de l'événement par l'injecteur à la première et à la
  dernière décision. C'est la réponse à « quel a été le temps de réponse ? ».
- **réseau** : latence aller simple par lien (appariement `SEND` / `RECV` par id).
- **traitement par nœud** : temps entre la réception d'un message et l'envoi suivant.

### Chronologie complète ou d'un seul événement

```console
$ python3 -m tools.timeline                          # tout
$ python3 -m tools.timeline --corr evt-e909-0002     # un seul événement
    t (ms)  nœud      thread           corr_id         événement      détail
     0.000  injector  MainThread       evt-e909-0002   SEND           GATE_UNAVAILABLE injector->flight id=24f5f5fc
     0.000  flight    flight-recv2     evt-e909-0002   RECV           GATE_UNAVAILABLE injector->flight id=24f5f5fc
     0.000  flight    flight-main      evt-e909-0002   GATE_DOWN      {"gate":"G12","flight":"TU701"}
     ...
     2.978  decision  decision-main    evt-e909-0002   DECISION       {"flight":"TU701","level":"CRITIQUE","risk":0.87,"gate":"G15",...}
$ python3 -m tools.timeline --csv experiments/results/e2e.csv   # export du résumé
```

### Résultat attendu pour `tu701`

| Événement | Décisions |
| --- | --- |
| 10:05 TU701 +20 min | TU701 **CRITIQUE** (risque 0,87), garde G12 ; TU703 SURVEILLE (0,65) ; TU702 AUCUNE (0,20) |
| 10:06 G12 indisponible | TU701 réaffecté à **G15**, 2 bus, coupe-file, notification |
| 10:07 file à 120 | attente 12 min > seuil : **CRITIQUE**, ouverture d'une file de sécurité |

## 5. Tests

```console
$ python3 -m pytest tests -v
tests/test_e2e_tu701.py::test_tu701_end_to_end PASSED
tests/test_protocol_net.py::test_roundtrip_and_prefix PASSED
tests/test_protocol_net.py::test_validation PASSED
tests/test_protocol_net.py::test_derive_keeps_chain PASSED
tests/test_protocol_net.py::test_corr_ids_unique_across_threads PASSED
tests/test_protocol_net.py::test_framing_survives_split_and_coalesced_reads PASSED
tests/test_protocol_net.py::test_server_and_sender_with_reconnect PASSED
```

`test_e2e_tu701.py` lance lui-même les 4 nœuds et rejoue `tu701` : **à relancer après
chaque branchement** de l'intégration en relais. Il est ignoré (`SKIPPED`) si des nœuds
tournent déjà sur les ports 5001 à 5004 : arrêter `run_all` avant.

## 6. Architecture

| Nœud | Port | Rôle | Tâches |
| --- | --- | --- | --- |
| `flight` | 5001 | Vols et ressources (portes) | T1 FlightMonitor |
| `ai` | 5002 | Inférence du risque | T3 AIInference |
| `decision` | 5003 | Politique et dispatcher | T4 DecisionResource |
| `passenger` | 5004 | File de sécurité et bus | T2 QueueMonitor, T5 SecurityAlert |

Les nœuds sont des **processus séparés** qui échangent des **messages JSON sur TCP**,
précédés de leur longueur sur 4 octets (annexe A du guide). Chaîne du scénario TU701 :

```
injector --FLIGHT_DELAY--> flight --RISK_REQUEST--> ai --RISK_RESULT--> decision
decision --GATE_REQUEST--> flight --GATE_ASSIGNED--> decision
decision --PAX_ACTION--> passenger --PAX_DONE--> decision --DECISION--> tous
injector --QUEUE_UPDATE--> passenger --SECURITY_ALERT--> decision
```

| Fichier | Contenu |
| --- | --- |
| `common/protocol.py` | `Message` (annexe A), types, sérialisation et découpage des trames |
| `common/net.py` | `NodeServer` (réception), `Sender` / `send` (connexions persistantes, reconnexion, `TCP_NODELAY`) |
| `common/config.py` | Ports, échelle de temps, graine 42, vols et portes du scénario |
| `common/logger.py` | Journal CSV `ts_ns, node, thread, corr_id, event, detail` |
| `nodes/base_node.py` | Squelette commun des nœuds |
| `scenarios/inject.py` | Injecteur pour le professeur |
| `tools/timeline.py` | Chronologie et temps de bout en bout |

Deux identifiants suivent chaque message :

- `id` : unique par message, apparie `SEND` et `RECV` (latence réseau) ;
- `corr_id` : identifiant de l'événement initial, recopié dans toute la chaîne avec
  `payload.t0_ns` (son horodatage) par `Message.derive`. Il donne `latency_ms` dans
  le message `DECISION`.

## 7. Brancher sa partie

Les nœuds ont aujourd'hui un **comportement factice** (mocks) :

| Nœud | Mock actuel | Remplacé par |
| --- | --- | --- |
| `ai` | Risques fixes du scénario (0,87 / 0,65 / 0,20), aléatoires pour les autres vols | `RiskPredictor` (P2) |
| `flight` | Garde la porte actuelle, sinon première porte libre | T1 (P3), `GateManager` (P4) |
| `decision` | Seuils fixes 0,7 / 0,4, un seul thread | `policy.decide` (P2), `SmartDispatcher` (P5) |
| `passenger` | Attente = file / 10 par min, bus de 25 places | T2, T5 (P3), `BusPool` (P4) |

Pour remplacer un mock : modifier le handler du nœud, ou en enregistrer un nouveau
dans `setup()`.

```python
from common.protocol import MessageType as T
from nodes.base_node import BaseNode

class AINode(BaseNode):
    name = "ai"

    def setup(self):
        self.on(T.RISK_REQUEST, self.on_risk_request)   # handler par type de message

    def on_risk_request(self, msg):
        # reply() recopie corr_id et t0_ns : la chronologie reste reliée
        self.reply(msg, T.RISK_RESULT, "decision", {"flight": "TU701", "risk": 0.87})
```

Le tampon de réception est une `queue.Queue` ; l'`EventBuffer` de P4 la remplace par
`BaseNode(inbox=EventBuffer(...))`.

Si une brique n'est pas prête lors de l'intégration, le système tourne avec le mock
correspondant : il reste toujours démontrable.

## 8. Dépannage

| Symptôme | Cause et solution |
| --- | --- |
| `ÉCHEC (nœud lancé ?)` dans l'injecteur | Les nœuds ne tournent pas : lancer `run_all` dans un autre terminal |
| `nœuds absents` / `Un nœud n'a pas démarré` | Lire `logs/<nœud>.err` (port déjà utilisé, erreur Python) |
| Port 5001 à 5004 déjà utilisé | Un ancien nœud tourne encore : `pkill -f nodes.` (Linux) ou fermer ses fenêtres (Windows) |
| `test_e2e_tu701` `SKIPPED` | Des nœuds tournent déjà : arrêter `run_all` |
| `HANDLER_ERROR` dans un journal | Exception dans un handler ; la trace est dans la colonne `detail`, le nœud continue |
| `run_all.ps1` : *Missing closing '}'* | Fichier réenregistré sans BOM : le réenregistrer en « UTF-8 with BOM » |
| WSL : `WSL_E_DISK_CORRUPTED` | Disque virtuel abîmé (souvent un disque C: plein) : libérer de l'espace, puis réparer ou réinstaller Ubuntu |

## Organisation

Une branche par partie (`p1-net`, `p2-ai`, `p3-rt`...), fusion dans `main` par pull
request relue ; `main` toujours exécutable. Graine aléatoire 42 partout. Le guide de
réalisation est dans `docs/`.

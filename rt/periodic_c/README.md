# rt/periodic_c/

**Responsable : P3 (bonus).** Tâche périodique en C avec `pthread` et `clock_nanosleep(TIMER_ABSTIME)`, pour comparer sa gigue à celle de la version Python.

## Compilation et lancement

Sous Linux ou WSL2 :

```bash
make
./periodic 10 2000 200 sortie.csv 0   # période (ms), jobs, charge (µs), CSV, cœur
```

Le CSV contient `job, release_ns, start_ns, end_ns`. Comme en Python, les réveils sont absolus et les réveils déjà passés sont sautés.

## Comparaison avec Python

Depuis la racine du dépôt : `python -m experiments.m3_jitter_c_vs_python`.

Exécution de référence du 23/11/2026, dans les [conditions de mesure](../README.md#conditions-de-mesure) du module : période 10 ms, 2000 jobs, 200 µs de CPU, processeur logique 0, CPython 3.14.4, GCC 15.2.0 avec `-O2`, sans `SCHED_FIFO`. Gigue de démarrage en µs :

| Version | Moyenne | p50 | p99 | Max |
| --- | ---: | ---: | ---: | ---: |
| Python | 350,8 | 270,5 | 930,7 | 11320,3 |
| C | 198,9 | 166,1 | 641,5 | 13423,3 |

Ces valeurs changent à chaque exécution. Dans celle-ci, C est meilleur sur la moyenne, le p50 et le p99.

**Le maximum ne permet pas de départager les deux versions.** Il dépend d'un seul job : C était nettement meilleur lors d'une exécution précédente (890 µs contre 7842 µs pour Python), puis moins bon dans celle-ci (13423 µs contre 11320 µs). Une seule tâche tourne ici, donc le GIL n'est pas en concurrence. Ces pics communs aux deux versions viennent probablement de la latence de réveil de WSL2 ou de l'activité de la machine hôte.

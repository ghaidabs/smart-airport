# experiments/

**Toute l'équipe.** Un script par expérience, relançable, avec la graine aléatoire 42.

Les résultats sont enregistrés dans `results/`. La graine rend le scénario injecté reproductible, mais pas les temps mesurés, qui changent à chaque exécution.

## M3 (P3)

Sous Linux ou WSL2, depuis la racine du dépôt, avec l'environnement virtuel activé : `bash experiments/run_m3.sh`

**Ce script écrase `rt/measured_wcet.json` et les résultats M3 de `results/`.** Pour conserver une exécution, copier ces fichiers ou les committer avant de relancer.

Les fichiers actuels correspondent à l'exécution de référence du 23/11/2026. Sa configuration est décrite dans [Conditions de mesure](../rt/README.md#conditions-de-mesure).

| Script | Rôle |
| --- | --- |
| `m3_wcet.py` | 1000 exécutions seules de T1, T2 et T5, C = max × 1,2, écrit `rt/measured_wcet.json` |
| `m3_load.py` | 60 s de charge normale, 60 s de charge × 2, graphique de Rmax et Gantt réel de TU701 |
| `m3_jitter_c_vs_python.py` | Compare la gigue de la même tâche périodique en Python et en C |
| `m3_common.py` | Assemble les tâches, les mocks de T3 et T4, et le générateur de charge |

Analyse et limites : voir [`rt/README.md`](../rt/README.md).

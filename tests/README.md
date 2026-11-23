# tests/

**Toute l'équipe.** Tests `pytest`, dont les invariants de synchronisation de P4.

Lancement depuis la racine du dépôt :

```bash
python -m pytest tests/
```

`test_rt_wiring.py` vérifie le branchement de T1 à T5 dans les nœuds. `test_e2e_tu701.py` lance les 4 nœuds réels : arrêter `run_all` avant, sinon il est ignoré.

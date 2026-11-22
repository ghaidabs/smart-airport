# ai/

**Responsable : P2.** Données, modèle de risque et réentraînement.

| Fichier | Rôle |
|---|---|
| `generate_data.py` | Simule 20 000 situations « groupe en correspondance » (graine 42) -> `data/connections.csv` |
| `train.py` | Découpage 70/15/15, régression logistique + gradient boosting, choix par score de Brier, métriques, courbes, sauvegarde atomique |
| `predictor.py` | `RiskPredictor` (annexe B) : chargement unique, préchauffage, prédiction par lot, repli par règle, rechargement à chaud |
| `retrain_cloud.py` | Tâche T6 : réentraîne et publie `risk-v2`, hors du chemin critique |
| `models/` | `model.joblib` + `model_meta.json` (version, ordre des variables, métriques) |

| `node_logic.py` | Logique du nœud IA (T3) : `RISK_REQUEST` -> un `RISK_RESULT` par vol, sans dépendance réseau |

Le nœud réseau est `nodes/ai_node.py` (sous-classe de `BaseNode` de P1), la politique `decision/policy.py`.
Les `RISK_RESULT` recopient `features` et `context` de la requête : le nœud Décision lit `n_connecting` et `connection_gate` dedans.

## Commandes (depuis la racine du dépôt)

```bash
python -m ai.generate_data               # données
python -m ai.train                       # modèle + courbes dans experiments/results/
python -m ai.predictor --demo            # risque des 3 vols du scénario
python -m ai.predictor --bench           # temps d'inférence -> C de T3
python -m ai.retrain_cloud --once        # publie risk-v2
python -m experiments.ai_evaluation      # rapport experiments/results/ai_report.md
python -m pytest tests/test_ai_*.py -v   # test_ai_integration.py nécessite common/ (P1)
```

## Contrat (annexe B)

```python
RiskPredictor().predict(features) -> {"risk": float, "model_version": str, "inference_ms": float}
# features : delay_min, slack_min, gate_distance_m, security_wait_min,
#            n_connecting, pax_total, hour, gate_changed
```

## Modèle en bref

Règle d'étiquetage : temps nécessaire = débarquement (5 à 15 min) + distance / vitesse
(50 à 90 m/min, variable latente) + attente sécurité + bruit N(0, 3 min) ; temps disponible =
slack - retard - 10. `missed = 1` si nécessaire > disponible. `n_connecting`, `hour` et
`gate_changed` n'entrent pas dans la règle (variables neutres, importance ~ 0).

L'IA estime un risque ; **elle ne garantit aucune deadline** : les garanties viennent de
l'ordonnancement (P5) et de la bande critique.
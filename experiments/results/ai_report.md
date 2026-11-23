# Évaluation du modèle de risque de correspondance manquée

Modèle retenu : **logreg** (version `risk-v1`, graine 42, 20000 situations ; découpage {'train': 14000, 'validation': 3000, 'test': 3000}).
Critère de choix : brier (validation).

## Comparaison des candidats (jeu de test, seuil 0,5, classe « manquée »)

| Modèle | Exactitude | Précision | Rappel | F1 | AUC ROC | Brier |
|---|---|---|---|---|---|---|
| logreg | 0.976 | 0.948 | 0.932 | 0.940 | 0.996 | 0.0189 |
| hist_gb | 0.966 | 0.930 | 0.899 | 0.914 | 0.994 | 0.0249 |

Matrice de confusion (logreg, test) : TN=2363 FP=31 FN=41 TP=565.

## Seuils de la politique (précision / rappel sur le test)

| Seuil | Précision | Rappel | Part des vols signalés |
|---|---|---|---|
| 0.4 | 0.926 | 0.949 | 20.7% |
| 0.7 | 0.976 | 0.870 | 18.0% |

## Importance des variables (baisse d'AUC par permutation)

| Variable | Importance |
|---|---|
| slack_min | 0.338 |
| delay_min | 0.070 |
| security_wait_min | 0.057 |
| gate_distance_m | 0.023 |
| pax_total | 0.006 |
| hour | 0.000 |
| n_connecting | 0.000 |
| gate_changed | 0.000 |

## Temps d'inférence (1000 appels unitaires)

moyenne 0.180 ms · p95 0.204 ms · max 0.677 ms -> **C(T3) = max x 1,2 = 0.81 ms** (D = 200 ms).

## Scénario illustratif (valeurs de `DEMO_FLIGHTS`)

| Vol | Risque | Niveau | Actions |
|---|---|---|---|
| TU701 | 0.90 | CRITIQUE | RESERVE_GATE_NEAREST, SECURITY_FAST_TRACK, NOTIFY |
| TU703 | 0.65 | SURVEILLE | NOTIFY, REPREDICT_ON_UPDATE |
| TU702 | 0.24 | AUCUNE | - |

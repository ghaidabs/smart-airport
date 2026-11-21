"""Rapport d'évaluation du modèle IA (livrable P2) -> experiments/results/ai_report.md

Rejoue l'expérience complète de façon reproductible (graine 42) :
données -> entraînement -> métriques, seuils, importance des variables ->
temps d'inférence (1000 appels) -> C de la tâche T3.

Usage (depuis la racine du dépôt) :
    python -m experiments.ai_evaluation
"""
from __future__ import annotations

import json
from pathlib import Path

from ai.generate_data import DEFAULT_OUT, N_DEFAULT, SEED, generate
from ai.predictor import DEMO_FLIGHTS, RiskPredictor
from ai.train import DEFAULT_MODEL_DIR, DEFAULT_REPORT_DIR, train
from decision.policy import decide


def main() -> None:
    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    generate(N_DEFAULT, SEED).to_csv(DEFAULT_OUT, index=False)
    meta = train(DEFAULT_OUT, DEFAULT_MODEL_DIR, DEFAULT_REPORT_DIR, version="risk-v1", seed=SEED)

    predictor = RiskPredictor(DEFAULT_MODEL_DIR)
    bench = predictor.benchmark(1000)
    results = predictor.predict_batch(list(DEMO_FLIGHTS.values()))

    best = meta["model_type"]
    lines = [
        "# Évaluation du modèle de risque de correspondance manquée",
        "",
        f"Modèle retenu : **{best}** (version `{meta['version']}`, graine {SEED}, "
        f"{meta['n_samples']} situations ; découpage {meta['split']}).",
        f"Critère de choix : {meta['selection_criterion']}.",
        "",
        "## Comparaison des candidats (jeu de test, seuil 0,5, classe « manquée »)",
        "",
        "| Modèle | Exactitude | Précision | Rappel | F1 | AUC ROC | Brier |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, res in meta["candidates"].items():
        t = res["test"]
        lines.append(
            f"| {name} | {t['accuracy']:.3f} | {t['precision']:.3f} | {t['recall']:.3f} | "
            f"{t['f1']:.3f} | {t['roc_auc']:.3f} | {t['brier']:.4f} |"
        )
    cm = meta["candidates"][best]["test"]["confusion_matrix"]
    lines += [
        "",
        f"Matrice de confusion ({best}, test) : TN={cm['tn']} FP={cm['fp']} FN={cm['fn']} TP={cm['tp']}.",
        "",
        "## Seuils de la politique (précision / rappel sur le test)",
        "",
        "| Seuil | Précision | Rappel | Part des vols signalés |",
        "|---|---|---|---|",
    ]
    for thr, r in meta["thresholds_test"].items():
        lines.append(f"| {thr} | {r['precision']:.3f} | {r['recall']:.3f} | {r['flagged_share']:.1%} |")
    lines += ["", "## Importance des variables (baisse d'AUC par permutation)", "", "| Variable | Importance |", "|---|---|"]
    for f, v in sorted(meta["feature_importance"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {f} | {v:.3f} |")
    lines += [
        "",
        "## Temps d'inférence (1000 appels unitaires)",
        "",
        f"moyenne {bench['mean_ms']:.3f} ms · p95 {bench['p95_ms']:.3f} ms · max {bench['max_ms']:.3f} ms "
        f"-> **C(T3) = max x 1,2 = {bench['c_ms_for_T3']:.2f} ms** (D = 200 ms).",
        "",
        "## Scénario illustratif (valeurs de `DEMO_FLIGHTS`)",
        "",
        "| Vol | Risque | Niveau | Actions |",
        "|---|---|---|---|",
    ]
    for name, res in zip(DEMO_FLIGHTS, results):
        d = decide(name, res["risk"], {"gate_distance_m": DEMO_FLIGHTS[name]["gate_distance_m"]})
        lines.append(f"| {name} | {res['risk']:.2f} | {d['level']} | {', '.join(d['actions']) or '-'} |")

    out = DEFAULT_REPORT_DIR / "ai_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DEFAULT_REPORT_DIR / "ai_inference_time.json").write_text(json.dumps(bench, indent=2))
    print(f"rapport écrit : {out}")


if __name__ == "__main__":
    main()
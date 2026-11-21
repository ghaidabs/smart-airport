"""Entraînement du modèle de risque de correspondance manquée.

Étapes : découpage stratifié 70/15/15 -> deux modèles candidats (régression
logistique de référence, gradient boosting plus riche) -> choix sur la
validation (score de Brier, car la probabilité sert de priorité) -> évaluation
sur le test -> sauvegarde ATOMIQUE de ``model.joblib`` et ``model_meta.json``.

Usage (depuis la racine du dépôt) :
    python -m ai.train
    python -m ai.train --data ai/data/connections.csv --version risk-v1
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")  # pas d'écran nécessaire (WSL, serveur)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.calibration import calibration_curve  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from ai.generate_data import DEFAULT_OUT as DEFAULT_DATA  # noqa: E402
from ai.generate_data import FEATURES, SEED, TARGET  # noqa: E402

AI_DIR = Path(__file__).resolve().parent
REPO_ROOT = AI_DIR.parent
DEFAULT_MODEL_DIR = AI_DIR / "models"
DEFAULT_REPORT_DIR = REPO_ROOT / "experiments" / "results"
POLICY_THRESHOLDS = (0.4, 0.7)  # seuils SURVEILLÉ / CRITIQUE (decision/policy.py)


def build_candidates(seed: int) -> dict:
    """Modèles candidats. Les deux produisent des probabilités (predict_proba)."""
    return {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
        "hist_gb": HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.1, max_iter=200, random_state=seed
        ),
    }


def split_data(df: pd.DataFrame, seed: int):
    """Découpage stratifié 70 / 15 / 15 (train / validation / test)."""
    X = df[FEATURES].to_numpy(dtype=float)  # ndarray : pas de noms de colonnes à l'inférence
    y = df[TARGET].to_numpy()
    X_tr, X_tmp, y_tr, y_tmp = train_test_split(X, y, test_size=0.30, stratify=y, random_state=seed)
    X_va, X_te, y_va, y_te = train_test_split(X_tmp, y_tmp, test_size=0.50, stratify=y_tmp, random_state=seed)
    return X_tr, X_va, X_te, y_tr, y_va, y_te


def evaluate(model, X, y) -> dict:
    """Métriques sur la classe positive « correspondance manquée »."""
    proba = model.predict_proba(X)[:, 1]
    pred = (proba >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred).ravel()
    return {
        "accuracy": accuracy_score(y, pred),
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "f1": f1_score(y, pred, zero_division=0),
        "roc_auc": roc_auc_score(y, proba),
        "brier": brier_score_loss(y, proba),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def threshold_report(model, X, y) -> dict:
    """Précision / rappel aux seuils de la politique (justification des seuils)."""
    proba = model.predict_proba(X)[:, 1]
    out = {}
    for t in POLICY_THRESHOLDS:
        pred = (proba >= t).astype(int)
        out[str(t)] = {
            "precision": precision_score(y, pred, zero_division=0),
            "recall": recall_score(y, pred, zero_division=0),
            "flagged_share": float(pred.mean()),
        }
    return out


def save_plots(model, X_te, y_te, report_dir: Path, importances: dict) -> None:
    """Courbes précision-rappel, calibration et importance des variables."""
    report_dir.mkdir(parents=True, exist_ok=True)
    proba = model.predict_proba(X_te)[:, 1]

    prec, rec, _ = precision_recall_curve(y_te, proba)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(rec, prec)
    for t in POLICY_THRESHOLDS:
        pred = (proba >= t).astype(int)
        ax.scatter(
            recall_score(y_te, pred), precision_score(y_te, pred, zero_division=0),
            label=f"seuil {t}", zorder=3,
        )
    ax.set(xlabel="Rappel", ylabel="Précision", title="Courbe précision-rappel (test)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(report_dir / "ai_precision_recall.png", dpi=150)
    plt.close(fig)

    frac_pos, mean_pred = calibration_curve(y_te, proba, n_bins=10, strategy="quantile")
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot([0, 1], [0, 1], "--", label="parfaite")
    ax.plot(mean_pred, frac_pos, marker="o", label="modèle")
    ax.set(xlabel="Probabilité prédite", ylabel="Fréquence observée", title="Calibration (test)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(report_dir / "ai_calibration.png", dpi=150)
    plt.close(fig)

    names = sorted(importances, key=importances.get)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.barh(names, [importances[n] for n in names])
    ax.set(xlabel="Baisse de l'AUC si la variable est permutée", title="Importance des variables (test)")
    fig.tight_layout()
    fig.savefig(report_dir / "ai_feature_importance.png", dpi=150)
    plt.close(fig)


def _atomic_write(path: Path, writer) -> None:
    """Écrit dans un fichier temporaire du même dossier puis ``os.replace`` (atomique)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        writer(Path(tmp))
        os.chmod(tmp, 0o644)  # mkstemp crée en 0600 ; on veut un fichier normal
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def publish_model(staging_dir: Path, live_dir: Path) -> None:
    """Publie un modèle entraîné dans ``live_dir`` : modèle d'abord, méta ensuite, de façon atomique."""
    live_dir = Path(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    for name in ("model.joblib", "model_meta.json"):
        data = (Path(staging_dir) / name).read_bytes()
        _atomic_write(live_dir / name, lambda p, d=data: p.write_bytes(d))


def train(
    data_path: Path = DEFAULT_DATA,
    model_dir: Path = DEFAULT_MODEL_DIR,
    report_dir: Path | None = DEFAULT_REPORT_DIR,
    version: str = "risk-v1",
    seed: int = SEED,
) -> dict:
    """Entraîne, évalue et publie le modèle. Renvoie les métadonnées écrites."""
    df = pd.read_csv(data_path)
    missing = set(FEATURES + [TARGET]) - set(df.columns)
    if missing:
        raise ValueError(f"colonnes manquantes dans {data_path} : {sorted(missing)}")

    X_tr, X_va, X_te, y_tr, y_va, y_te = split_data(df, seed)

    results, fitted = {}, {}
    for name, model in build_candidates(seed).items():
        model.fit(X_tr, y_tr)
        fitted[name] = model
        results[name] = {"validation": evaluate(model, X_va, y_va), "test": evaluate(model, X_te, y_te)}

    best = min(results, key=lambda n: results[n]["validation"]["brier"])  # choix sur la validation
    model = fitted[best]

    perm = permutation_importance(model, X_te, y_te, scoring="roc_auc", n_repeats=5, random_state=seed)
    importances = {f: float(v) for f, v in zip(FEATURES, perm.importances_mean)}

    meta = {
        "version": version,
        "model_type": best,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "features": FEATURES,
        "n_samples": int(len(df)),
        "split": {"train": len(y_tr), "validation": len(y_va), "test": len(y_te)},
        "selection_criterion": "brier (validation)",
        "candidates": results,
        "thresholds_test": threshold_report(model, X_te, y_te),
        "feature_importance": importances,
    }

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    # Le modèle d'abord, les métadonnées ensuite : un lecteur qui voit la nouvelle
    # version dans meta est sûr de trouver le nouveau modèle.
    _atomic_write(model_dir / "model.joblib", lambda p: joblib.dump(model, p))
    _atomic_write(model_dir / "model_meta.json", lambda p: p.write_text(json.dumps(meta, indent=2)))

    if report_dir is not None:
        save_plots(model, X_te, y_te, Path(report_dir), importances)
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--version", default="risk-v1")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    meta = train(args.data, args.model_dir, args.report_dir, args.version, args.seed)
    print(f"modèle retenu : {meta['model_type']} (version {meta['version']})")
    for name, res in meta["candidates"].items():
        t = res["test"]
        print(
            f"  {name:8s} test: recall={t['recall']:.3f} precision={t['precision']:.3f} "
            f"f1={t['f1']:.3f} auc={t['roc_auc']:.3f} brier={t['brier']:.4f}"
        )
    print("importance des variables :", {k: round(v, 3) for k, v in meta["feature_importance"].items()})
    print(f"fichiers : {args.model_dir}/model.joblib, model_meta.json")


if __name__ == "__main__":
    main()
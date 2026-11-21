"""Tâche T6 « Cloud simulé » : réentraînement hors du chemin critique.

Pourquoi un processus séparé (justification TEMPORELLE, exigée par l'énoncé) :
    * l'inférence prend ~1 ms et doit rester LOCALE sur le nœud Edge (T3, D = 200 ms) ;
    * l'entraînement prend des secondes et n'a AUCUNE deadline : il ne doit jamais
      voler du temps de processeur aux tâches critiques.
C'est une tâche apériodique de priorité basse : le processus se met en priorité
réduite (``os.nice``) et ne communique avec le nœud IA que par fichiers.

Cycle : données fraîches (autre graine) -> entraînement dans un dossier de
travail -> contrôle qualité -> publication atomique (modèle puis méta). Le nœud IA
détecte la nouvelle version et l'échange entre deux requêtes (``reload_if_changed``).

Usage (depuis la racine du dépôt) :
    python -m ai.retrain_cloud --once                 # un cycle -> risk-v2
    python -m ai.retrain_cloud --interval 60          # un cycle toutes les 60 s
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from ai.generate_data import SEED, generate
from ai.train import DEFAULT_MODEL_DIR, publish_model, train

LOG = logging.getLogger("ai.retrain_cloud")

MIN_RECALL = 0.80   # contrôle qualité : on ne publie pas un modèle dégradé
MIN_AUC = 0.90
VERSION_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")


def next_version(current: str) -> str:
    """``risk-v1`` -> ``risk-v2`` ; une version sans numéro final devient ``<version>-v2``."""
    m = VERSION_RE.match(current)
    if not m:
        return f"{current}-v2"
    return f"{m['prefix']}{int(m['num']) + 1}"


def retrain_once(model_dir: Path = DEFAULT_MODEL_DIR, n_samples: int = 20_000, base_seed: int = SEED) -> dict | None:
    """Exécute un cycle. Renvoie les métadonnées publiées, ou None si le contrôle qualité échoue."""
    model_dir = Path(model_dir)
    current = json.loads((model_dir / "model_meta.json").read_text())["version"]
    version = next_version(current)
    number = int(VERSION_RE.match(version)["num"]) if VERSION_RE.match(version) else 2
    seed = base_seed + number  # données « nouvelles » mais reproductibles

    with tempfile.TemporaryDirectory(prefix="retrain_") as tmp:
        tmp = Path(tmp)
        data_path = tmp / "data.csv"
        generate(n_samples, seed=seed).to_csv(data_path, index=False)
        t0 = time.perf_counter()
        meta = train(data_path, tmp / "staging", report_dir=None, version=version, seed=seed)
        duration_s = time.perf_counter() - t0

        test = meta["candidates"][meta["model_type"]]["test"]
        if test["recall"] < MIN_RECALL or test["roc_auc"] < MIN_AUC:
            LOG.warning("modèle %s rejeté (recall=%.3f auc=%.3f)", version, test["recall"], test["roc_auc"])
            return None
        publish_model(tmp / "staging", model_dir)

    LOG.info("modèle %s publié (entraînement %.1f s, recall=%.3f)", version, duration_s, test["recall"])
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--n", type=int, default=20_000, help="taille du jeu de données frais")
    parser.add_argument("--once", action="store_true", help="un seul cycle puis arrêt")
    parser.add_argument("--interval", type=float, default=60.0, help="secondes entre deux cycles")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[cloud] %(message)s")
    if hasattr(os, "nice"):  # absent sous Windows natif
        os.nice(10)          # priorité basse : ne gêne pas les nœuds Edge

    while True:
        meta = retrain_once(args.model_dir, args.n)
        if meta:
            print(f"[cloud] nouvelle version publiée : {meta['version']}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
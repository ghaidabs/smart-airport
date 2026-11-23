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

MIN_RECALL = 0.80 
MIN_AUC = 0.90
VERSION_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")


def next_version(current: str) -> str:
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
    seed = base_seed + number  

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
    if hasattr(os, "nice"):  
        os.nice(10)          
    while True:
        meta = retrain_once(args.model_dir, args.n)
        if meta:
            print(f"[cloud] nouvelle version publiée : {meta['version']}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
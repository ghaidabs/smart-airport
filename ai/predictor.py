
from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

import joblib
import numpy as np

from ai.generate_data import BOARDING_CLOSE_MIN, FEATURES, deboarding_min

LOG = logging.getLogger("ai.predictor")

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parent.parent / "experiments" / "results"
FALLBACK_VERSION = "fallback-rule"
WCET_MARGIN = 1.2  


class _State:
    """Instantané immuable : modèle + ordre des variables + version."""

    __slots__ = ("model", "features", "version")

    def __init__(self, model, features: list[str], version: str) -> None:
        self.model, self.features, self.version = model, features, version


def rule_based_risk(f: Mapping[str, float]) -> float:
    """Repli : mêmes ingrédients que le générateur, vitesse moyenne de 70 m/min.

    Le risque est une sigmoïde de l'écart (nécessaire - disponible), d'échelle 3 min
    (l'écart-type du bruit du générateur).
    """
    needed = float(deboarding_min(f["pax_total"])) + f["gate_distance_m"] / 70.0 + f["security_wait_min"]
    available = f["slack_min"] - f["delay_min"] - BOARDING_CLOSE_MIN
    z = max(-50.0, min(50.0, (needed - available) / 3.0))
    return 1.0 / (1.0 + math.exp(-z))


class RiskPredictor:
    """Charge le modèle entraîné et prédit le risque de correspondance manquée."""

    def __init__(self, model_dir: str | Path | None = None, warmup: bool = True) -> None:
        self._dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self._lock = threading.Lock()
        self._state = self._load_state()  # échoue vite si le modèle est absent
        if warmup:
            self._warmup()

    def _read_meta(self) -> dict:
        return json.loads((self._dir / "model_meta.json").read_text())

    def _load_state(self) -> _State:
        """Charge modèle + métadonnées de façon cohérente, même si le « Cloud » publie.

        La publication écrit model.joblib puis model_meta.json. On lit donc la méta,
        on charge le modèle, et on relit la méta : si la version a changé entre-temps,
        on recommence (au plus 3 fois).
        """
        for _ in range(3):
            meta = self._read_meta()
            model = joblib.load(self._dir / "model.joblib")
            if self._read_meta()["version"] == meta["version"]:
                break
        features = list(meta["features"])
        if features != FEATURES:
            raise ValueError(f"ordre des variables inattendu : {features} != {FEATURES}")
        return _State(model, features, str(meta["version"]))

    def _warmup(self) -> None:
        sample = {f: 1.0 for f in FEATURES}
        self.predict_batch([sample])

    def reload(self) -> bool:
        """Recharge le modèle depuis le disque. Renvoie True si la version a changé.

        Le nouveau modèle est entièrement construit AVANT l'échange ; la section
        critique ne contient qu'une affectation (quelques microsecondes).
        """
        new_state = self._load_state()
        with self._lock:
            changed = new_state.version != self._state.version
            self._state = new_state
        self._warmup()
        return changed

    def reload_if_changed(self) -> bool:
        """Recharge seulement si une nouvelle version est publiée (lecture d'un petit JSON).

        Appelée par le nœud IA ENTRE deux requêtes ; renvoie True si le modèle a changé.
        Une publication partielle ou illisible est ignorée (on garde l'ancien modèle).
        """
        try:
            if self._read_meta()["version"] == self.model_version:
                return False
            return self.reload()
        except (OSError, ValueError, KeyError):
            LOG.warning("publication illisible, ancien modèle conservé", exc_info=True)
            return False

    @property
    def model_version(self) -> str:
        with self._lock:
            return self._state.version

    def _vectorize(self, batch: Sequence[Mapping[str, float]], names: list[str]) -> np.ndarray:
        rows = []
        for i, feats in enumerate(batch):
            missing = [n for n in names if n not in feats]
            if missing:
                raise ValueError(f"variables manquantes (élément {i}) : {missing}")
            row = [float(feats[n]) for n in names]
            if not all(math.isfinite(v) for v in row):
                raise ValueError(f"valeur non finie dans l'élément {i} : {feats}")
            rows.append(row)
        return np.asarray(rows, dtype=float)

    def predict_batch(self, batch: Sequence[Mapping[str, float]]) -> list[dict]:
        """Prédit pour plusieurs vols en UN appel (un RISK_REQUEST contient une liste).

        Chaque résultat porte le temps d'inférence du lot entier.
        """
        if not batch:
            return []
        with self._lock:
            state = self._state  # instantané : cohérent même si reload() survient
        X = self._vectorize(batch, state.features)  # ValueError = bug de l'appelant, on propage

        t0 = time.perf_counter_ns()
        try:
            risks = state.model.predict_proba(X)[:, 1].tolist()
            version = state.version
        except Exception:  
            LOG.exception("inférence en échec, repli sur la règle")
            risks = [rule_based_risk(f) for f in batch]
            version = FALLBACK_VERSION
        ms = (time.perf_counter_ns() - t0) / 1e6
        return [{"risk": float(r), "model_version": version, "inference_ms": ms} for r in risks]

    def predict(self, features: Mapping[str, float]) -> dict:
        """Contrat de l'annexe B pour un seul vol."""
        return self.predict_batch([features])[0]

    def benchmark(self, n: int = 1000, seed: int = 42) -> dict:
        """Mesure le temps d'inférence unitaire sur ``n`` appels (valeurs variées)."""
        rng = np.random.default_rng(seed)
        samples = [
            {
                "delay_min": rng.uniform(0, 60), "slack_min": rng.uniform(35, 120),
                "gate_distance_m": rng.uniform(100, 1500), "security_wait_min": rng.uniform(0, 40),
                "n_connecting": int(rng.integers(1, 61)), "pax_total": int(rng.integers(80, 301)),
                "hour": int(rng.integers(6, 24)), "gate_changed": int(rng.integers(0, 2)),
            }
            for _ in range(n)
        ]
        times = np.array([self.predict(s)["inference_ms"] for s in samples])
        return {
            "n_calls": n,
            "model_version": self.model_version,
            "mean_ms": float(times.mean()),
            "p95_ms": float(np.percentile(times, 95)),
            "max_ms": float(times.max()),
            "c_ms_for_T3": float(times.max() * WCET_MARGIN),
        }


# Valeurs illustratives du scénario (à remplacer par celles de scenarios/tu701.json
# quand P1 les aura figées). Voir README du dossier ai/.
DEMO_FLIGHTS = {
    "TU701": dict(delay_min=20, slack_min=55, gate_distance_m=450, security_wait_min=12,
                  n_connecting=35, pax_total=240, hour=10, gate_changed=1),
    "TU703": dict(delay_min=28, slack_min=75, gate_distance_m=900, security_wait_min=15,
                  n_connecting=20, pax_total=200, hour=10, gate_changed=0),
    "TU702": dict(delay_min=12, slack_min=55, gate_distance_m=600, security_wait_min=12,
                  n_connecting=12, pax_total=180, hour=10, gate_changed=0),
}


def _demo(p: RiskPredictor) -> None:
    results = p.predict_batch(list(DEMO_FLIGHTS.values()))
    print(f"modèle {p.model_version} - inférence du lot : {results[0]['inference_ms']:.2f} ms")
    for name, res in zip(DEMO_FLIGHTS, results):
        print(f"  {name}: risque = {res['risk']:.2f}")
    after = dict(DEMO_FLIGHTS["TU701"], gate_distance_m=150, security_wait_min=2)
    print(f"  TU701 après G15 (150 m) + coupe-file (2 min) : risque = {p.predict(after)['risk']:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--bench", action="store_true")
    parser.add_argument("--n", type=int, default=1000)
    args = parser.parse_args()

    p = RiskPredictor(args.model_dir)
    if args.demo or not args.bench:
        _demo(p)
    if args.bench:
        res = p.benchmark(args.n)
        DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = DEFAULT_REPORT_DIR / "ai_inference_time.json"
        out.write_text(json.dumps(res, indent=2))
        print(json.dumps(res, indent=2))
        print(f"-> {out}")


if __name__ == "__main__":
    main()
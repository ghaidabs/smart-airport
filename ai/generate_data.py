from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_DEFAULT = 20_000
NOISE_SD_MIN = 3.0          # écart-type du bruit gaussien (minutes)
BOARDING_CLOSE_MIN = 10.0   # l'embarquement ferme 10 min avant le départ

FEATURES = [
    "delay_min",
    "slack_min",
    "gate_distance_m",
    "security_wait_min",
    "n_connecting",
    "pax_total",
    "hour",
    "gate_changed",
]
TARGET = "missed"

DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "connections.csv"


def deboarding_min(pax_total):
    """Durée de débarquement : 5 min (80 pax) à 15 min (300 pax), linéaire."""
    pax = np.clip(pax_total, 80, 300)
    return 5.0 + 10.0 * (pax - 80.0) / (300.0 - 80.0)


def required_time_min(pax_total, gate_distance_m, security_wait_min, speed_m_per_min):
    """Temps nécessaire hors bruit : débarquement + marche + sécurité."""
    return (
        deboarding_min(pax_total)
        + np.asarray(gate_distance_m) / np.asarray(speed_m_per_min)
        + np.asarray(security_wait_min)
    )


def available_time_min(slack_min, delay_min):
    """Temps disponible avant la fermeture de l'embarquement du vol suivant."""
    return np.asarray(slack_min) - np.asarray(delay_min) - BOARDING_CLOSE_MIN


def generate(n: int = N_DEFAULT, seed: int = SEED, noise_sd: float = NOISE_SD_MIN) -> pd.DataFrame:
    """Génère ``n`` situations. Déterministe pour une graine donnée."""
    if n <= 0:
        raise ValueError("n doit être strictement positif")
    rng = np.random.default_rng(seed)

    delay = np.clip(rng.exponential(scale=10.0, size=n), 0, 90).round(1)  # surtout petits
    slack = rng.uniform(35, 120, n).round(1)
    distance = rng.uniform(100, 1500, n).round(0)
    sec_wait = np.clip(rng.exponential(scale=10.0, size=n), 0, 40).round(1)
    n_conn = rng.integers(1, 61, n)
    pax_total = rng.integers(80, 301, n)
    hour = rng.integers(6, 24, n)
    gate_changed = rng.binomial(1, 0.25, n)

    speed = rng.uniform(50, 90, n)             # latente, non fournie au modèle
    noise = rng.normal(0.0, noise_sd, n)

    needed = required_time_min(pax_total, distance, sec_wait, speed) + noise
    available = available_time_min(slack, delay)
    missed = (needed > available).astype(int)

    return pd.DataFrame(
        {
            "delay_min": delay,
            "slack_min": slack,
            "gate_distance_m": distance,
            "security_wait_min": sec_wait,
            "n_connecting": n_conn,
            "pax_total": pax_total,
            "hour": hour,
            "gate_changed": gate_changed,
            TARGET: missed,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=N_DEFAULT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--noise", type=float, default=NOISE_SD_MIN, help="écart-type du bruit (min)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    df = generate(args.n, args.seed, args.noise)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"{len(df)} lignes -> {args.out}")
    print(f"taux de correspondances manquées : {df[TARGET].mean():.1%}")


if __name__ == "__main__":
    main()
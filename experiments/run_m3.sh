#!/usr/bin/env bash
# Relance toutes les expériences M3 depuis la racine du dépôt (environ 3 min 30).
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"

"$PYTHON" -m experiments.m3_wcet
"$PYTHON" -m experiments.m3_load "$@"
"$PYTHON" -m experiments.m3_jitter_c_vs_python
"$PYTHON" -m rt.tasks

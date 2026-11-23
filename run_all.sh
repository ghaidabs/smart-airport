#!/usr/bin/env bash
# Lance les 4 nœuds dans des processus séparés (P1).
#
#   ./run_all.sh            # lance les nœuds, Ctrl+C pour tout arrêter
#   ./run_all.sh --keep-logs   # ne vide pas logs/*.csv au démarrage
#
# Puis, dans un autre terminal :
#   python3 -m scenarios.inject tu701
#   python3 -m tools.timeline
#   python3 -m rt.report      # après Ctrl+C : mesures réelles de T1 à T5
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
NODES=(flight ai decision passenger)

if [[ "${1:-}" != "--keep-logs" ]]; then
    rm -f logs/*.csv logs/rt/*.csv
fi
mkdir -p logs

pids=()
cleanup() {
    echo
    echo "Arrêt des nœuds..."
    for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for node in "${NODES[@]}"; do
    "$PYTHON" -m "nodes.${node}_node" > "logs/${node}.out" 2>&1 &
    pids+=($!)
done

if "$PYTHON" -m common.net 10; then
    echo "4 nœuds prêts (ports 5001-5004). Ctrl+C pour arrêter."
    echo "Sorties console : logs/<nœud>.out ; journaux : logs/<nœud>.csv"
else
    echo "Un nœud n'a pas démarré, voir logs/*.out" >&2
    exit 1
fi

wait

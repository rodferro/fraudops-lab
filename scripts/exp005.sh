#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
STATE="$PWD/artifacts/EXP-005"
PYTHON="${EXP005_PYTHON:-$STATE/venv/bin/python}"
export MPLCONFIGDIR="$STATE/matplotlib"
export XDG_CACHE_HOME="$STATE/cache"
mkdir -p "$MPLCONFIGDIR" "$XDG_CACHE_HOME"
case "${1:-help}" in
  setup)
    # Python 3.12 is the recorded environment; override PYTHON_BOOTSTRAP if needed.
    "${PYTHON_BOOTSTRAP:-python3.12}" -m venv "$STATE/venv"
    "$PYTHON" -m pip install -r scripts/exp005-requirements.txt
    ;;
  test)
    "$PYTHON" -m unittest discover -s scripts -p test_exp005.py -v
    ;;
  run)
    "$PYTHON" scripts/exp005_run.py --kubeconfig "$PWD/artifacts/EXP-003/kubeconfig" \
      --output "${2:-$STATE/run-$(date -u +%Y%m%dT%H%M%SZ)}"
    ;;
  analyze)
    "$PYTHON" scripts/exp005_analyze.py "${2:?supply a raw run directory}" \
      --output "${3:?supply a new aggregate output directory}"
    ;;
  cleanup)
    # Use after an interrupted run; never change the user's default context.
    kubectl --kubeconfig "$PWD/artifacts/EXP-003/kubeconfig" --context kind-fraudops-exp003 \
      -n fraudops-exp003 delete deployment fraudops-exp005-cpu --ignore-not-found --wait=false
    ;;
  *)
    echo 'Usage: bash scripts/exp005.sh {setup|test|run [new-run-dir]|analyze raw-dir new-aggregate-dir|cleanup}'
    ;;
esac

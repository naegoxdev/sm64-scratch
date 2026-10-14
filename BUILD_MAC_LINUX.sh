#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================"
echo " Mario 64 Scratch - build from your own ROM"
echo "============================================================"

PYTHON="${PYTHON:-python3}"
"$PYTHON" --version

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment..."
  "$PYTHON" -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

ROM="${1:-}"
if [ -z "$ROM" ]; then
  printf "Path to your US Super Mario 64 ROM (.z64/.n64/.v64): "
  IFS= read -r ROM
fi

if [ ! -f "$ROM" ]; then
  echo "ERROR: File not found: $ROM" >&2
  exit 1
fi

mkdir -p output
python build_game.py --rom "$ROM" --out "output/MARIO64_SCRATCH.sb3" --report "output/build_report.json"

echo
echo "READY: output/MARIO64_SCRATCH.sb3"
echo "Report: output/build_report.json"

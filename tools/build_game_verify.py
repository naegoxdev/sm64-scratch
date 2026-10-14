#!/usr/bin/env python3
"""Build the existing silent diagnostic project."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sm64_scratch.build_game import main

if __name__ == "__main__":
    main(diagnostic_default=True)

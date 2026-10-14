#!/usr/bin/env python3
"""Build the playable project; implementation lives in src/sm64_scratch."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from sm64_scratch.build_game import main

if __name__ == "__main__":
    main()

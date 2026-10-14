"""Repository paths shared by the builder and resource decoders."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
METADATA_DIR = REPO_ROOT / "config" / "metadata"
BASE_PATH = REPO_ROOT / "templates" / "BASE.sb3"
OUTPUT_DIR = REPO_ROOT / "output"

"""Load config.json with ~ path expansion."""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"


def load() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    paths = cfg["data_paths"]
    paths["chrome_history"] = Path(paths["chrome_history"]).expanduser()
    paths["clipboard_db"] = Path(paths["clipboard_db"]).expanduser()
    return cfg

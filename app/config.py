"""Paths and settings. Importing this module loads <repo>/.env into os.environ (existing vars win)."""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_env(REPO / ".env")

DATA_DIR = REPO / "data"
MEETINGS_DIR = DATA_DIR / "meetings"
INDEX_DB = DATA_DIR / "index.db"
WEB_DIR = REPO / "web"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("MC_LLM_MODEL", "nemotron-3.5-lightning")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY")

NOTES_INTERVAL = 60
ROLLCALL_TIMEOUT = 60

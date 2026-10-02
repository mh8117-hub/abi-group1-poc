"""Configuration loader. Reads settings from environment variables or a local .env file.

The .env file is git-ignored: it holds a personal Open WebUI token and must never be committed.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "OPENWEBUI_BASE_URL": "http://172.22.42.174:8080",
    "MODEL_ID": "llama-3.1-8b-instruct",
    "TEMPERATURE": "0",
    "SEED": "42",
    "MAX_TOKENS": "600",
    "TIMEOUT_S": "120",
}


def _read_dotenv(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(_read_dotenv(REPO_ROOT / ".env"))
    for key in list(DEFAULTS) + ["OPENWEBUI_TOKEN"]:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    if not cfg.get("OPENWEBUI_TOKEN"):
        raise SystemExit(
            "OPENWEBUI_TOKEN is not set. Put it in .env (see .env.example) or the environment."
        )
    return cfg

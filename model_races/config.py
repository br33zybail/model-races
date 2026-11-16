"""Connection settings and the role roster, read from the environment and a
TOML file. Nothing here is specific to one machine.

Environment:
  RACE_BASE_URL    OpenAI-compatible base URL (default http://localhost:4000/v1)
  RACE_API_KEY     bearer token sent to the gateway (default "local")
  RACE_OLLAMA_URL  Ollama chat URL, used only by the local sweep roster
  RACE_ROSTER      path to a roster TOML (default roster.toml, else roster.example.toml)
  RACE_SPEND_LOG   JSONL file of gateway spend records (see gw_spend.py)
  RACE_LEDGER      optional script that records topology-race edges
"""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BASE_URL = os.environ.get("RACE_BASE_URL", "http://localhost:4000/v1").rstrip("/")
CHAT_URL = BASE_URL + "/chat/completions"
API_KEY = os.environ.get("RACE_API_KEY", "local")
OLLAMA_URL = os.environ.get("RACE_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
LEDGER = os.environ.get("RACE_LEDGER", "")


def headers():
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"}


def runs_dir():
    d = ROOT / "runs"
    d.mkdir(exist_ok=True)
    return d


def _roster_path():
    if os.environ.get("RACE_ROSTER"):
        return Path(os.environ["RACE_ROSTER"])
    own = ROOT / "roster.toml"
    return own if own.exists() else ROOT / "roster.example.toml"


ROSTER = tomllib.loads(_roster_path().read_text())

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.json"
_ENV_PATH = _REPO_ROOT / ".env"

_ENV_OVERRIDES = {
    "host": "INFLUXDB_HOST",
    "port": "INFLUXDB_PORT",
    "database": "INFLUXDB_DB",
    "username": "INFLUXDB_ADMIN_USER",
    "password": "INFLUXDB_ADMIN_PASSWORD",
}


def _parse_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    except (FileNotFoundError, OSError):
        pass
    return env


def _load_config_file() -> Dict[str, Any]:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def load_config(section: str) -> Dict[str, Any]:
    """Return the named section from config.json.

    For the 'influxdb' section, environment variables (and .env) take
    precedence over the file, so deployment-specific values (host, password)
    can always be overridden without editing the committed config.
    """
    data = _load_config_file().get(section, {})

    if section == "influxdb":
        file_env = _parse_env_file(_ENV_PATH)
        env_view = {**file_env, **os.environ}
        for field, env_key in _ENV_OVERRIDES.items():
            raw = env_view.get(env_key)
            if raw:
                data[field] = int(raw) if field == "port" else raw

    return data

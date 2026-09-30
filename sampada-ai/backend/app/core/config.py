"""Configuration access for the SAMPADA AI backend.

``config.toml`` is the single source of truth. Secrets (API keys) are never
stored in the file - they are read from the environment via
:func:`env_secret`, so that the config can be committed safely.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

# app/core/config.py -> app/core -> app -> backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("SAMPADA_CONFIG") or (BACKEND_DIR / "config.toml"))
LOCALES_DIR = Path(__file__).resolve().parents[1] / "locales"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class Settings:
    """Dotted-path reader over the parsed TOML document.

    A dotted-path reader (instead of one attribute per setting) keeps callers
    honest about missing keys: :meth:`require` raises rather than silently
    returning ``None`` and letting a default quietly change system behaviour.
    """

    def __init__(self, data: dict[str, Any], path: Path) -> None:
        self._data = data
        self.path = path

    def _walk(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def get(self, dotted: str, default: Any = None) -> Any:
        return self._walk(dotted, default)

    def require(self, dotted: str) -> Any:
        sentinel = object()
        value = self._walk(dotted, sentinel)
        if value is sentinel:
            raise KeyError(f"required config key missing: {dotted} in {self.path}")
        return value

    def section(self, dotted: str) -> dict[str, Any]:
        value = self.get(dotted, {})
        return value if isinstance(value, dict) else {}

    # Convenience accessors for settings used in many places -------------

    @property
    def supported_languages(self) -> list[str]:
        return list(self.get("language.supported", ["mr", "hi", "en"]))

    @property
    def default_language(self) -> str:
        return str(self.get("language.default", "mr"))

    @property
    def data_mode(self) -> str:
        """Either ``"live"`` or ``"sample"``.

        ``sample`` is the only way demo data may enter the system, and every
        sample-derived response is tagged as demo at the provider boundary.
        """
        mode = str(self.get("data.mode", "live")).lower()
        return mode if mode in {"live", "sample"} else "live"

    @property
    def sample_mode(self) -> bool:
        return self.data_mode == "sample"


def load_settings(path: Path | None = None) -> Settings:
    path = Path(path) if path else CONFIG_PATH
    with open(path, "rb") as handle:
        return Settings(tomllib.load(handle), path)


def env_secret(env_var: str) -> str | None:
    """Read a secret from the environment. Returns ``None`` when unset/empty."""
    value = os.environ.get(env_var, "").strip()
    return value or None


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from the ``.env`` file next to the config file.

    Secrets travel with their config: the file is looked up beside whatever
    ``config.toml`` this process loaded (``backend/.env`` by default), which
    also means a test run pointed at a temporary config sees no developer
    secrets at all. The `.gitignore` already expects secrets in ``.env``;
    this gives that convention effect with no third-party dependency. Rules:

    - a variable already present in the environment is **never** overwritten,
      so an exported value always wins over the file;
    - blank lines and ``#`` comments are skipped; an optional ``export ``
      prefix and one layer of matching quotes are stripped;
    - the file is read once at import time and its contents are never logged.
    """
    path = CONFIG_PATH.parent / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

settings = load_settings()

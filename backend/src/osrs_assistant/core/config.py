"""Runtime configuration, read from environment variables.

Nothing personal is hard-coded so the repo stays safe to publish.

    OSRS_ASSISTANT_CONTACT   Contact for the User-Agent (e.g. "@you on Discord").
                             The OSRS Wiki asks API users to include one.
    OSRS_DEFAULT_RSN         Your RuneScape name, used when a tool call omits `rsn`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from osrs_assistant import __version__

PROJECT_URL = "https://github.com/your-username/gielinor-guide"

# backend/.env (gitignored). src/osrs_assistant/core/config.py -> parents[3] == backend/
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load KEY=value lines into os.environ. Real environment variables always win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    contact: str | None
    default_rsn: str | None

    @property
    def user_agent(self) -> str:
        ua = f"gielinor-guide/{__version__} (OSRS helper for Claude; +{PROJECT_URL})"
        if self.contact:
            ua += f" - {self.contact}"
        return ua


def load_settings() -> Settings:
    load_env_file()

    def _env(name: str) -> str | None:
        value = os.environ.get(name, "").strip()
        return value or None

    return Settings(
        contact=_env("OSRS_ASSISTANT_CONTACT"),
        default_rsn=_env("OSRS_DEFAULT_RSN"),
    )

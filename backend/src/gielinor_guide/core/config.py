"""Runtime configuration, read from environment variables.

Nothing personal is hard-coded so the repo stays safe to publish.

    GIELINOR_GUIDE_CONTACT   Contact for the User-Agent (e.g. "@you on Discord").
                             The OSRS Wiki asks API users to include one.
    OSRS_DEFAULT_RSN         Your RuneScape name, used when a tool call omits `rsn`.
    RUNELITE_EXPORT_DIR      Where the RuneLite "Data Export" plugin writes files.
                             Default: ~/.runelite/Data Exports
    GIELINOR_GUIDE_DATA_DIR  Where local history (SQLite) is stored.
                             Default: ~/.local/share/gielinor-guide
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gielinor_guide import __version__

PROJECT_URL = "https://github.com/wally0220/gielinor-guide"

# backend/.env (gitignored). src/gielinor_guide/core/config.py -> parents[3] == backend/
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


DEFAULT_EXPORT_DIR = Path.home() / ".runelite" / "Data Exports"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "gielinor-guide"


@dataclass(frozen=True)
class Settings:
    contact: str | None
    default_rsn: str | None
    export_dir: Path = DEFAULT_EXPORT_DIR
    data_dir: Path = DEFAULT_DATA_DIR

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
        contact=_env("GIELINOR_GUIDE_CONTACT"),
        default_rsn=_env("OSRS_DEFAULT_RSN"),
        export_dir=Path(_env("RUNELITE_EXPORT_DIR") or DEFAULT_EXPORT_DIR).expanduser(),
        data_dir=Path(_env("GIELINOR_GUIDE_DATA_DIR") or DEFAULT_DATA_DIR).expanduser(),
    )

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def hiscores_sample() -> dict[str, Any]:
    """Synthetic response shaped like index_lite.json (not a real player)."""
    data: dict[str, Any] = load_fixture("hiscores_sample.json")
    return data

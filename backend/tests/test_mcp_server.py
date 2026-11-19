"""End-to-end through a real MCP client, in memory (no subprocess, no network)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from mcp.client.client import Client

from osrs_assistant.core.config import Settings
from osrs_assistant.core.http import OSRSHttp
from osrs_assistant.mcp.server import build_server

URL = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.json"


def make_server(default_rsn: str | None = "SamplePlayer") -> Any:
    http = OSRSHttp(Settings(contact=None, default_rsn=default_rsn), min_interval=0)
    return build_server(http)


@pytest.mark.anyio
async def test_lists_tools() -> None:
    async with Client(make_server()) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    assert {"get_player_stats", "xp_calc"} <= tools


@pytest.mark.anyio
@respx.mock
async def test_get_player_stats_uses_default_rsn(hiscores_sample: dict[str, Any]) -> None:
    route = respx.get(URL, params={"player": "SamplePlayer"}).mock(
        return_value=httpx.Response(200, json=hiscores_sample)
    )
    async with Client(make_server()) as client:
        result = await client.call_tool("get_player_stats", {"skills": ["mage", "smith"]})

    assert route.called
    assert not result.is_error
    data = result.structured_content
    assert data is not None
    assert data["combat_level"] == 101
    assert [s["skill"] for s in data["skills"]] == ["Magic", "Smithing"]
    magic = data["skills"][0]
    assert magic["level"] == 73
    assert magic["xp_to_next_level"] == 1_096_278 - 1_010_000


@pytest.mark.anyio
@respx.mock
async def test_xp_calc_from_hiscores(hiscores_sample: dict[str, Any]) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=hiscores_sample))
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "xp_calc",
            {
                "skill": "magic",
                "target_level": 75,
                "methods": [{"name": "Lvl-5 Enchant", "xp_each": 78}],
            },
        )
    data = result.structured_content
    assert data is not None
    assert data["source"] == "hiscores"
    assert data["xp_remaining"] == 1_210_421 - 1_010_000
    assert data["methods"][0]["actions_needed"] == 2_570  # ceil(200421 / 78)


@pytest.mark.anyio
async def test_xp_calc_from_level_needs_no_network() -> None:
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "xp_calc",
            {
                "skill": "smithing",
                "target_level": 70,
                "current_level": 50,
                "methods": [{"name": "Gold bar (gauntlets)", "xp_each": 56.2}],
            },
        )
    data = result.structured_content
    assert data is not None
    assert data["methods"][0]["actions_needed"] == 11_322


@pytest.mark.anyio
async def test_unknown_skill_is_a_clean_tool_error() -> None:
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "xp_calc", {"skill": "dungeoneering", "target_level": 50, "current_level": 1}
        )
    assert result.is_error
    assert "Unknown skill" in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_missing_rsn_without_default() -> None:
    async with Client(make_server(default_rsn=None)) as client:
        result = await client.call_tool("get_player_stats", {})
    assert result.is_error
    assert "OSRS_DEFAULT_RSN" in result.content[0].text  # type: ignore[union-attr]

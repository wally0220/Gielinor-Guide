"""suggest_training through an in-memory MCP client: data sources degrade gracefully."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.client.client import Client

from gielinor_guide.core.config import Settings
from gielinor_guide.core.http import OSRSHttp
from gielinor_guide.mcp.server import build_server

HISCORES = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.json"
WIKISYNC = "https://sync.runescape.wiki/runelite/player/SamplePlayer/STANDARD"


def make_server(tmp_path: Path, export_dir: Path | None = None) -> Any:
    settings = Settings(
        contact=None,
        default_rsn="SamplePlayer",
        export_dir=export_dir or tmp_path / "missing",
        data_dir=tmp_path / "data",
    )
    return build_server(OSRSHttp(settings, min_interval=0))


@pytest.mark.anyio
@respx.mock
async def test_suggest_training_uses_all_sources(
    tmp_path: Path,
    export_dir: Path,
    hiscores_sample: dict[str, Any],
    wikisync_sample: dict[str, Any],
) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    respx.get(WIKISYNC).mock(return_value=httpx.Response(200, json=wikisync_sample))
    async with Client(make_server(tmp_path, export_dir)) as client:
        result = await client.call_tool("suggest_training", {"skill": "smith"})
    data = result.structured_content
    assert data is not None and not result.is_error
    assert data["skill"] == "Smithing" and data["target_level"] == 51
    assert data["data_sources"] == {"hiscores": "ok", "wikisync": "ok", "runelite_export": "ok"}

    top = data["methods"][0]
    assert top["id"] == "blast_furnace_gold"
    assert top["owned_covers_percent"] == 100.0  # 500 ore in the fixture bank
    assert top["recipes"][0]["ingredients"][0]["item"] == "Gold ore"
    assert top["xp_per_hour"] == "266k-380k" and top["approximate"] is False
    assert top["requirements"][0]["status"] == "met"  # gauntlets equipped
    assert top["hours"].endswith(" h")
    assert data["route"][0]["method"] == top["name"]
    assert any("already own" in n for n in data["notes"])
    assert "ranges" in data["disclaimer"]


@pytest.mark.anyio
@respx.mock
async def test_missing_optional_sources_degrade_to_unknown(
    tmp_path: Path, hiscores_sample: dict[str, Any]
) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    respx.get(WIKISYNC).mock(return_value=httpx.Response(404))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "suggest_training", {"skill": "thieving", "target_level": 60}
        )
    data = result.structured_content
    assert data is not None and not result.is_error
    assert data["data_sources"]["wikisync"].startswith("unavailable")
    assert data["data_sources"]["runelite_export"].startswith("unavailable")
    assert len(data["notes"]) >= 2
    statuses = {r["status"] for m in data["methods"] for r in m["requirements"]}
    assert "missing" not in statuses


@pytest.mark.anyio
@respx.mock
async def test_route_methods_get_boost_tips(
    tmp_path: Path,
    export_dir: Path,
    hiscores_sample: dict[str, Any],
    wikisync_sample: dict[str, Any],
) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    respx.get(WIKISYNC).mock(return_value=httpx.Response(200, json=wikisync_sample))
    async with Client(make_server(tmp_path, export_dir)) as client:
        result = await client.call_tool("suggest_training", {"skill": "smithing"})
    data = result.structured_content
    assert data is not None
    assert data["route"][0]["method"] == "Gold bars at the Blast Furnace"
    # The fixture bank has ice gloves but no stamina potions.
    assert any("Stamina potion(4) would help" in n for n in data["notes"])
    assert not any("Ice gloves would help" in n for n in data["notes"])


@pytest.mark.anyio
@respx.mock
async def test_slayer_carries_variance_notice(
    tmp_path: Path, hiscores_sample: dict[str, Any]
) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "suggest_training",
            {"skill": "slayer", "target_level": 40, "use_account_data": False},
        )
    data = result.structured_content
    assert data is not None and not result.is_error
    assert data["notes"][0].startswith("Slayer rates are rough approximations")
    assert "Slayer master" in data["notes"][0] and "gear" in data["notes"][0]
    assert all(m["approximate"] and m["variance"] for m in data["methods"])


@pytest.mark.anyio
@respx.mock
async def test_missing_tools_are_listed(
    tmp_path: Path, export_dir: Path, hiscores_sample: dict[str, Any]
) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    respx.get(WIKISYNC).mock(return_value=httpx.Response(404))
    async with Client(make_server(tmp_path, export_dir)) as client:
        result = await client.call_tool(
            "suggest_training", {"skill": "thieving", "target_level": 60}
        )
    data = result.structured_content
    assert data is not None
    assert any(n.startswith("Bring or buy:") and "Lockpick" in n for n in data["notes"])


@pytest.mark.anyio
@respx.mock
async def test_skip_account_data(tmp_path: Path, hiscores_sample: dict[str, Any]) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "suggest_training",
            {"skill": "herblore", "target_level": 65, "use_account_data": False, "max_methods": 1},
        )
    data = result.structured_content
    assert data is not None
    assert data["data_sources"]["wikisync"] == "skipped"
    assert len(data["methods"]) == 1
    assert [s["method"] for s in data["route"]][-1] == "Super restore potions"


@pytest.mark.anyio
@respx.mock
async def test_no_combined_runs_note_when_runs_cannot_start(
    tmp_path: Path, hiscores_sample: dict[str, Any]
) -> None:
    data = copy.deepcopy(hiscores_sample)
    for entry in data["skills"]:
        if entry["name"] == "Farming":
            entry["level"], entry["xp"] = 5, 388  # below every run method's first tier
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=data))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "suggest_training",
            {"skill": "farming", "target_level": 20, "use_account_data": False},
        )
    out = result.structured_content
    assert out is not None and not result.is_error
    assert out["combined_runs"] is None
    assert not any("combined_runs" in n for n in out["notes"])


@pytest.mark.anyio
@respx.mock
async def test_unlocks_later_lists_missing_requirements(
    tmp_path: Path, hiscores_sample: dict[str, Any], wikisync_sample: dict[str, Any]
) -> None:
    stats = copy.deepcopy(hiscores_sample)
    for entry in stats["skills"]:
        if entry["name"] == "Thieving":
            entry["level"], entry["xp"] = 40, 37_224
    progress = copy.deepcopy(wikisync_sample)
    progress["quests"]["Children of the Sun"] = 0
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=stats))
    respx.get(WIKISYNC).mock(return_value=httpx.Response(200, json=progress))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "suggest_training", {"skill": "thieving", "target_level": 60}
        )
    out = result.structured_content
    assert out is not None and not result.is_error
    later = {m["id"]: m for m in out["unlocks_later"]}
    assert later["wealthy_citizens"]["missing"] == ["Children of the Sun (not started)"]
    assert all(m["missing"] == [] for m in out["methods"])


@pytest.mark.anyio
@respx.mock
async def test_uncovered_skill_is_clean_error(
    tmp_path: Path, hiscores_sample: dict[str, Any]
) -> None:
    respx.get(HISCORES).mock(return_value=httpx.Response(200, json=hiscores_sample))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "suggest_training", {"skill": "woodcutting", "use_account_data": False}
        )
    assert result.is_error
    assert "Skills covered so far" in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_description_lists_covered_skills(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    description = tools["suggest_training"].description or ""
    assert "Thieving" in description and "{covered}" not in description

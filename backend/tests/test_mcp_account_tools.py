"""Account tools end-to-end through an in-memory MCP client (no network, temp files only)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.client.client import Client

from gielinor_guide.core.config import Settings
from gielinor_guide.core.http import OSRSHttp
from gielinor_guide.core.store import Store
from gielinor_guide.mcp.server import build_server

WIKISYNC = "https://sync.runescape.wiki/runelite/player/SamplePlayer/STANDARD"
NOW = datetime(2026, 11, 19, 5, 0, tzinfo=UTC)  # 31 min after the fixture's sync timestamp


def make_server(tmp_path: Path, export_dir: Path | None = None) -> Any:
    settings = Settings(
        contact=None,
        default_rsn="SamplePlayer",
        export_dir=export_dir or tmp_path / "missing",
        data_dir=tmp_path / "data",
    )
    return build_server(
        OSRSHttp(settings, min_interval=0), store=Store(tmp_path / "h.db"), now=lambda: NOW
    )


@pytest.mark.anyio
async def test_tools_are_listed(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {"get_account_progress", "import_bank"} <= names


@pytest.mark.anyio
@respx.mock
async def test_account_progress_summary(tmp_path: Path, wikisync_sample: dict[str, Any]) -> None:
    respx.get(WIKISYNC).mock(return_value=httpx.Response(200, json=wikisync_sample))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool(
            "get_account_progress", {"quests": ["legends quest", "Dragon Slayer II", "nope"]}
        )
    data = result.structured_content
    assert data is not None and not result.is_error
    assert data["minutes_since_sync"] == 31
    assert data["quests_completed"] == 3
    assert data["quests_in_progress"] == ["Contact!", "Regicide"]
    assert data["completed_quest_names"] is None  # summary mode
    assert [(q["quest"], q["state"]) for q in data["quest_lookup"]] == [
        ("Legends' Quest", "completed"),
        ("Dragon Slayer II", "not_started"),
        (None, "unknown"),
    ]
    ardougne = data["diaries"][0]
    assert ardougne["completed_tiers"] == ["Easy"]
    assert ardougne["tasks"]["Medium"] == "4/12"
    assert any("Collection log not synced" in n for n in data["notes"])


@pytest.mark.anyio
@respx.mock
async def test_account_progress_full_and_stale(
    tmp_path: Path, wikisync_sample: dict[str, Any]
) -> None:
    wikisync_sample["timestamp"] = "2026-11-15T05:00:00Z"
    respx.get(WIKISYNC).mock(return_value=httpx.Response(200, json=wikisync_sample))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool("get_account_progress", {"detail": "full"})
    data = result.structured_content
    assert data is not None
    assert "While Guthix Sleeps" in data["not_started_quest_names"]
    assert any("96h old" in n for n in data["notes"])


@pytest.mark.anyio
@respx.mock
async def test_account_not_synced_is_clean_error(tmp_path: Path) -> None:
    respx.get(WIKISYNC).mock(return_value=httpx.Response(404))
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool("get_account_progress", {})
    assert result.is_error
    assert "WikiSync" in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_import_bank_search_coins_and_equipment(tmp_path: Path, export_dir: Path) -> None:
    async with Client(make_server(tmp_path, export_dir)) as client:
        result = await client.call_tool("import_bank", {"search": "graceful"})
    data = result.structured_content
    assert data is not None and not result.is_error
    assert data["coins"] == 1_234_567 + 2_000  # coins + 2 platinum tokens
    assert data["equipped"] == ["Goldsmith gauntlets", "Graceful boots"]
    assert {(r["container"], r["name"]) for r in data["search_results"]} == {
        ("bank", "Graceful gloves"),
        ("inventory", "Graceful boots"),
        ("equipment", "Graceful boots"),
    }
    assert data["items"] is None
    assert [c["container"] for c in data["containers"]] == ["bank", "inventory", "equipment"]
    assert all(c["snapshot_saved"] for c in data["containers"])


@pytest.mark.anyio
async def test_import_bank_reports_changes_between_exports(
    tmp_path: Path, export_dir: Path
) -> None:
    bank = export_dir / "container_bank.json"
    os.utime(bank, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    async with Client(make_server(tmp_path, export_dir)) as client:
        await client.call_tool("import_bank", {"containers": ["bank"]})
        again = await client.call_tool("import_bank", {"containers": ["bank"]})

        # Smelt 500 gold ore, then the plugin writes a newer export.
        bank.write_text(
            bank.read_text().replace(
                '{"id": 444, "quantity": 500, "name": "Gold ore"}',
                '{"id": 2357, "quantity": 500, "name": "Gold bar"}',
            )
        )
        os.utime(bank, (NOW.timestamp() - 60, NOW.timestamp() - 60))
        after = await client.call_tool("import_bank", {"containers": ["bank"]})

    assert again.structured_content is not None
    assert again.structured_content["containers"][0]["snapshot_saved"] is False
    data = after.structured_content
    assert data is not None
    bank_out = data["containers"][0]
    assert bank_out["snapshot_saved"] is True
    assert bank_out["minutes_old"] == 1
    assert {(c["name"], c["delta"]) for c in bank_out["changes_since_previous"]} == {
        ("Gold bar", 500),
        ("Gold ore", -500),
    }


@pytest.mark.anyio
async def test_import_bank_notes_missing_containers(tmp_path: Path, export_dir: Path) -> None:
    (export_dir / "container_equipment.json").unlink()
    async with Client(make_server(tmp_path, export_dir)) as client:
        result = await client.call_tool("import_bank", {"list_items": True})
    data = result.structured_content
    assert data is not None
    assert data["equipped"] == []
    assert any("equipment" in n for n in data["notes"])
    assert len(data["items"]) == 9


@pytest.mark.anyio
async def test_import_bank_without_exports_is_clean_error(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool("import_bank", {})
    assert result.is_error
    assert "Data Export" in result.content[0].text  # type: ignore[union-attr]

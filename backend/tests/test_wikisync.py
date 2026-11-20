from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from gielinor_guide.core.config import Settings
from gielinor_guide.core.errors import AccountNotSyncedError, UpstreamError
from gielinor_guide.core.http import OSRSHttp
from gielinor_guide.core.wikisync import QuestState, fetch_progress, normalize_name, parse_wikisync

BASE = "https://sync.runescape.wiki/runelite/player"
SETTINGS = Settings(contact="test@example.com", default_rsn=None)


def test_parse_skips_junk_quest_key_and_counts(wikisync_sample: dict[str, Any]) -> None:
    p = parse_wikisync("SamplePlayer", wikisync_sample)
    assert "." not in p.quests
    assert len(p.quests) == 8
    assert p.quests_in(QuestState.IN_PROGRESS) == ["Contact!", "Regicide"]
    assert p.synced_at == datetime(2026, 11, 19, 4, 28, 51, 673000, tzinfo=UTC)
    assert p.combat_achievement_ids == [32, 521]
    assert p.collection_log_ids == []
    assert (p.music_unlocked, p.music_total) == (2, 3)


def test_quest_lookup_forgives_punctuation(wikisync_sample: dict[str, Any]) -> None:
    p = parse_wikisync("SamplePlayer", wikisync_sample)
    assert p.find_quest("legends quest") == ("Legends' Quest", QuestState.COMPLETED)
    assert p.find_quest("DRAGON SLAYER II") == ("Dragon Slayer II", QuestState.NOT_STARTED)
    assert p.find_quest("contact") == ("Contact!", QuestState.IN_PROGRESS)
    assert p.find_quest("dragon slayer 3") is None


def test_diaries(wikisync_sample: dict[str, Any]) -> None:
    p = parse_wikisync("SamplePlayer", wikisync_sample)
    easy = p.diary("Ardougne", "easy")
    assert easy is not None and easy.complete and (easy.tasks_done, easy.tasks_total) == (10, 10)
    kourend = p.diary("kourend and kebos", "Easy")
    assert kourend is not None and not kourend.complete and kourend.tasks_done == 3
    # Tiers come out in game order regardless of the payload's (alphabetical) order.
    assert [d.tier for d in p.diaries if d.region == "Ardougne"] == [
        "Easy",
        "Medium",
        "Hard",
        "Elite",
    ]


def test_normalize_name() -> None:
    assert normalize_name("Kourend & Kebos") == normalize_name("kourend and kebos")
    assert normalize_name("Legends' Quest") == normalize_name("Legends Quest")


def test_parse_rejects_bad_payloads() -> None:
    with pytest.raises(UpstreamError):
        parse_wikisync("x", {"username": "x"})
    with pytest.raises(UpstreamError):
        parse_wikisync("x", {"quests": {}, "timestamp": "not a date"})


@pytest.mark.anyio
@respx.mock
async def test_fetch_caches_and_sends_user_agent(wikisync_sample: dict[str, Any]) -> None:
    route = respx.get(f"{BASE}/SamplePlayer/STANDARD").mock(
        return_value=httpx.Response(200, json=wikisync_sample)
    )
    http = OSRSHttp(SETTINGS, min_interval=0)
    try:
        await fetch_progress(http, "SamplePlayer")
        await fetch_progress(http, "SamplePlayer")
    finally:
        await http.aclose()
    assert route.call_count == 1
    assert "test@example.com" in route.calls[0].request.headers["User-Agent"]


@pytest.mark.anyio
@respx.mock
async def test_rsn_with_space_is_url_encoded(wikisync_sample: dict[str, Any]) -> None:
    route = respx.get(f"{BASE}/Some%20Player/STANDARD").mock(
        return_value=httpx.Response(200, json=wikisync_sample)
    )
    http = OSRSHttp(SETTINGS, min_interval=0)
    try:
        await fetch_progress(http, "Some Player")
    finally:
        await http.aclose()
    assert route.called


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 404])
@respx.mock
async def test_not_synced(status: int) -> None:
    respx.get(f"{BASE}/Nobody/STANDARD").mock(return_value=httpx.Response(status))
    http = OSRSHttp(SETTINGS, min_interval=0)
    try:
        with pytest.raises(AccountNotSyncedError):
            await fetch_progress(http, "Nobody")
    finally:
        await http.aclose()

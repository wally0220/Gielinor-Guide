from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from osrs_assistant.core.config import Settings
from osrs_assistant.core.errors import PlayerNotFoundError, UpstreamError
from osrs_assistant.core.hiscores import fetch_player, parse_hiscores
from osrs_assistant.core.http import OSRSHttp

URL = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.json"
SETTINGS = Settings(contact="test@example.com", default_rsn=None)


def test_parse_levels_combat_and_unranked(hiscores_sample: dict[str, Any]) -> None:
    stats = parse_hiscores("SamplePlayer", "normal", hiscores_sample)
    assert stats.level("Attack") == 82
    assert stats.level("mage") == 73  # alias
    assert stats.xp("magic") == 1_010_000
    # 0.25*(75+82+35) + 0.325*(82+84) = 48 + 53.95 -> 101
    assert stats.combat_level == 101
    assert stats.skills["Sailing"].ranked is False
    assert stats.skills["Sailing"].level is None
    # Activities with score -1 are dropped.
    assert {a.name for a in stats.activities} == {"Clue Scrolls (all)", "Zulrah"}


def test_parse_rank_capped_skill_keeps_xp(hiscores_sample: dict[str, Any]) -> None:
    # Outside the top ~2M, Jagex sends rank -1 but still reports level and xp.
    data = {**hiscores_sample, "skills": [dict(s) for s in hiscores_sample["skills"]]}
    ranged = next(s for s in data["skills"] if s["name"] == "Ranged")
    ranged["rank"] = -1
    stats = parse_hiscores("SamplePlayer", "normal", data)
    assert stats.skills["Ranged"].rank is None
    assert stats.skills["Ranged"].ranked is True
    assert stats.level("Ranged") == 75
    assert stats.xp("Ranged") == 1_252_428


def test_parse_rejects_garbage() -> None:
    with pytest.raises(UpstreamError):
        parse_hiscores("x", "normal", {"nope": []})


@pytest.mark.anyio
@respx.mock
async def test_fetch_sends_user_agent_and_caches(hiscores_sample: dict[str, Any]) -> None:
    route = respx.get(URL, params={"player": "SamplePlayer"}).mock(
        return_value=httpx.Response(200, json=hiscores_sample)
    )
    http = OSRSHttp(SETTINGS, min_interval=0)
    try:
        first = await fetch_player(http, "SamplePlayer")
        second = await fetch_player(http, "SamplePlayer")
    finally:
        await http.aclose()

    assert first == second
    assert route.call_count == 1  # second call served from cache
    ua = route.calls[0].request.headers["User-Agent"]
    assert ua.startswith("gielinor-guide/") and "test@example.com" in ua


@pytest.mark.anyio
@respx.mock
async def test_unknown_player_404() -> None:
    respx.get(URL).mock(return_value=httpx.Response(404))
    http = OSRSHttp(SETTINGS, min_interval=0)
    try:
        with pytest.raises(PlayerNotFoundError):
            await fetch_player(http, "NoSuchPlayer1")
    finally:
        await http.aclose()


@pytest.mark.anyio
@respx.mock
async def test_retries_on_503(hiscores_sample: dict[str, Any], monkeypatch: Any) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("osrs_assistant.core.http.asyncio.sleep", no_sleep)
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=hiscores_sample)]
    )
    http = OSRSHttp(SETTINGS, min_interval=0)
    try:
        stats = await fetch_player(http, "SamplePlayer")
    finally:
        await http.aclose()
    assert route.call_count == 2
    assert stats.level("Construction") == 85

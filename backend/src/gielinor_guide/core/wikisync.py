"""WikiSync: account progress the hiscores can't see (quests, diaries, CAs, collection log).

WikiSync is a RuneLite plugin that uploads progress to a server run by the OSRS Wiki.
Endpoint: https://sync.runescape.wiki/runelite/player/<rsn>/<profile>

Observed payload (Nov 2026):
    {"username", "timestamp" (ISO 8601, UTC),
     "quests": {name: 0 | 1 | 2},            # not started / in progress / completed
     "achievement_diaries": {region: {tier: {"complete": bool, "tasks": [bool, ...]}}},
     "levels": {skill: int},
     "music_tracks": {name: bool},
     "combat_achievements": [task_id, ...],  # completed task ids
     "collection_log": [item_id, ...],       # only after pressing "WikiSync" in the log
     "league_tasks", "bingo_tasks", "collectionLogItemCount", "sea_charting"}

Quirks handled here: a junk "." quest key, null fields, and quest names with apostrophes that
users type without them ("Legends Quest").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, Literal
from urllib.parse import quote

from gielinor_guide.core.errors import AccountNotSyncedError, UpstreamError
from gielinor_guide.core.hiscores import GameMode
from gielinor_guide.core.http import OSRSHttp

Profile = Literal["STANDARD", "LEAGUE", "DEADMAN", "BETA", "SEASONAL"]

BASE_URL = "https://sync.runescape.wiki/runelite/player/{rsn}/{profile}"

# WikiSync profiles follow RuneLite's world types, hiscores tables follow Jagex's game modes.
# Ironmen play on standard worlds, so they share the STANDARD profile with normal accounts.
_MODE_FOR_PROFILE: dict[str, GameMode | None] = {
    "STANDARD": "normal",
    "SEASONAL": "seasonal",
    "LEAGUE": "seasonal",
    "DEADMAN": "deadman",
    "BETA": None,  # beta worlds have no hiscores
}
_PROFILE_FOR_MODE: dict[str, Profile | None] = {
    "normal": "STANDARD",
    "ironman": "STANDARD",
    "hardcore": "STANDARD",
    "ultimate": "STANDARD",
    "seasonal": "SEASONAL",
    "deadman": "DEADMAN",
    "fresh_start": None,  # no matching WikiSync profile
}


def hiscores_mode_for_profile(profile: Profile) -> GameMode | None:
    """Hiscores table holding the same character as this WikiSync profile (None: no table)."""
    return _MODE_FOR_PROFILE[profile]


def profile_for_mode(mode: GameMode) -> Profile | None:
    """WikiSync profile for the character on this hiscores table (None: no profile)."""
    return _PROFILE_FOR_MODE[mode]


WIKISYNC_TTL = 600.0  # seconds; the plugin pushes on change, so 10 minutes is plenty fresh

DIARY_TIERS = ("Easy", "Medium", "Hard", "Elite")


class QuestState(IntEnum):
    NOT_STARTED = 0
    IN_PROGRESS = 1
    COMPLETED = 2


@dataclass(frozen=True)
class DiaryTier:
    region: str
    tier: str
    complete: bool
    tasks_done: int
    tasks_total: int


@dataclass(frozen=True)
class AccountProgress:
    rsn: str
    synced_at: datetime
    quests: dict[str, QuestState]
    diaries: list[DiaryTier]
    levels: dict[str, int]
    combat_achievement_ids: list[int] = field(default_factory=list)
    collection_log_ids: list[int] = field(default_factory=list)
    music_unlocked: int = 0
    music_total: int = 0

    def quests_in(self, state: QuestState) -> list[str]:
        return sorted(name for name, s in self.quests.items() if s == state)

    def find_quest(self, name: str) -> tuple[str, QuestState] | None:
        """Look a quest up by name, forgiving case, punctuation and apostrophes."""
        key = normalize_name(name)
        for quest, state in self.quests.items():
            if normalize_name(quest) == key:
                return quest, state
        return None

    def diary(self, region: str, tier: str) -> DiaryTier | None:
        r, t = normalize_name(region), normalize_name(tier)
        for d in self.diaries:
            if normalize_name(d.region) == r and normalize_name(d.tier) == t:
                return d
        return None

    def age_seconds(self, now: datetime | None = None) -> float:
        return ((now or datetime.now(UTC)) - self.synced_at).total_seconds()


def normalize_name(name: str) -> str:
    """'Legends Quest' == "Legends' Quest"; 'kourend and kebos' == 'Kourend & Kebos'."""
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("&", "and"))


def _is_real_quest(name: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", name))


def parse_wikisync(rsn: str, data: dict[str, Any]) -> AccountProgress:
    if not isinstance(data, dict) or "quests" not in data:
        raise UpstreamError("WikiSync response is missing the 'quests' section")

    try:
        synced_at = datetime.fromisoformat(str(data["timestamp"]))
    except (KeyError, ValueError) as exc:
        raise UpstreamError("WikiSync response has no valid 'timestamp'") from exc
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=UTC)

    quests = {
        name: QuestState(int(state))
        for name, state in (data.get("quests") or {}).items()
        if _is_real_quest(name) and int(state) in (0, 1, 2)
    }

    diaries: list[DiaryTier] = []
    for region, tiers in (data.get("achievement_diaries") or {}).items():
        for tier in DIARY_TIERS:
            info = (tiers or {}).get(tier)
            if not info:
                continue
            tasks = [bool(t) for t in info.get("tasks") or []]
            diaries.append(
                DiaryTier(
                    region=region,
                    tier=tier,
                    complete=bool(info.get("complete")),
                    tasks_done=sum(tasks),
                    tasks_total=len(tasks),
                )
            )

    music = data.get("music_tracks") or {}
    return AccountProgress(
        rsn=str(data.get("username") or rsn),
        synced_at=synced_at,
        quests=quests,
        diaries=diaries,
        levels={k: int(v) for k, v in (data.get("levels") or {}).items()},
        combat_achievement_ids=[int(i) for i in data.get("combat_achievements") or []],
        collection_log_ids=[int(i) for i in data.get("collection_log") or []],
        music_unlocked=sum(1 for v in music.values() if v),
        music_total=len(music),
    )


async def fetch_progress(
    http: OSRSHttp, rsn: str, profile: Profile = "STANDARD"
) -> AccountProgress:
    rsn = rsn.strip()
    if not rsn or len(rsn) > 12:
        raise AccountNotSyncedError(rsn)
    data = await http.get_json(
        BASE_URL.format(rsn=quote(rsn), profile=profile),
        ttl=WIKISYNC_TTL,
        not_found=lambda: AccountNotSyncedError(rsn),
        # Unknown players have come back as 400 as well as 404 from similar services.
        not_found_statuses=frozenset({400, 404}),
    )
    return parse_wikisync(rsn, data)

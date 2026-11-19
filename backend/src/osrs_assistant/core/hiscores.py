"""Official OSRS hiscores (Jagex).

Endpoint: https://secure.runescape.com/m=<mode>/index_lite.json?player=<rsn>
Returns {"name": ..., "skills": [{id, name, rank, level, xp}], "activities": [{id, name, rank,
score}]}. Skills come in two "unranked" flavours:
- Outside the rank cap (~top 2M per skill): rank is -1 but level and xp are still real.
- Never listed: rank and xp are both -1 (true level unknown). Activities use score -1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from osrs_assistant.core.errors import PlayerNotFoundError, UpstreamError
from osrs_assistant.core.http import OSRSHttp
from osrs_assistant.core.skills import normalize_skill

GameMode = Literal[
    "normal", "ironman", "hardcore", "ultimate", "deadman", "seasonal", "fresh_start"
]

MODE_PATHS: dict[str, str] = {
    "normal": "hiscore_oldschool",
    "ironman": "hiscore_oldschool_ironman",
    "hardcore": "hiscore_oldschool_hardcore_ironman",
    "ultimate": "hiscore_oldschool_ultimate",
    "deadman": "hiscore_oldschool_deadman",
    "seasonal": "hiscore_oldschool_seasonal",
    "fresh_start": "hiscore_oldschool_fresh_start",
}

BASE_URL = "https://secure.runescape.com/m={path}/index_lite.json"
HISCORES_TTL = 180.0  # seconds; hiscores don't change faster than you can play


@dataclass(frozen=True)
class SkillStat:
    name: str
    level: int | None
    """None when the skill is unranked (too low to appear on the hiscores)."""
    xp: int | None
    rank: int | None
    """None when outside the hiscores rank cap, even if level/xp are known."""

    @property
    def ranked(self) -> bool:
        return self.xp is not None


@dataclass(frozen=True)
class Activity:
    name: str
    score: int
    rank: int


@dataclass(frozen=True)
class PlayerStats:
    rsn: str
    mode: str
    overall: SkillStat
    skills: dict[str, SkillStat]
    activities: list[Activity] = field(default_factory=list)

    def level(self, skill: str) -> int | None:
        return self.skills[normalize_skill(skill)].level

    def xp(self, skill: str) -> int | None:
        return self.skills[normalize_skill(skill)].xp

    @property
    def combat_level(self) -> int | None:
        return combat_level(self)


def _num(value: Any) -> int | None:
    n = int(value)
    return None if n < 0 else n


def parse_hiscores(rsn: str, mode: str, data: dict[str, Any]) -> PlayerStats:
    try:
        raw_skills = data["skills"]
    except (KeyError, TypeError) as exc:
        raise UpstreamError("Hiscores response is missing the 'skills' list") from exc

    overall: SkillStat | None = None
    skills: dict[str, SkillStat] = {}
    for entry in raw_skills:
        xp = _num(entry["xp"])
        stat = SkillStat(
            name=entry["name"],
            level=_num(entry["level"]) if xp is not None else None,
            xp=xp,
            rank=_num(entry["rank"]),
        )
        if stat.name == "Overall":
            overall = stat
        else:
            skills[stat.name] = stat

    if overall is None:
        raise UpstreamError("Hiscores response has no 'Overall' entry")

    activities = [
        Activity(name=a["name"], score=int(a["score"]), rank=int(a["rank"]))
        for a in data.get("activities", [])
        if int(a.get("score", -1)) >= 0
    ]
    return PlayerStats(
        rsn=data.get("name", rsn), mode=mode, overall=overall, skills=skills, activities=activities
    )


def combat_level(stats: PlayerStats) -> int | None:
    """Standard OSRS combat formula. Returns None if a combat skill is unranked."""

    def lvl(skill: str, default: int = 1) -> int:
        s = stats.skills.get(skill)
        return s.level if s and s.level is not None else default

    # Unranked Hitpoints defaults to 10, everything else to 1: this is a lower bound.
    att, strength, defence = lvl("Attack"), lvl("Strength"), lvl("Defence")
    hp, prayer = lvl("Hitpoints", 10), lvl("Prayer")
    ranged, magic = lvl("Ranged"), lvl("Magic")

    base = 0.25 * (defence + hp + prayer // 2)
    melee = 0.325 * (att + strength)
    rng = 0.325 * (ranged * 3 // 2)
    mage = 0.325 * (magic * 3 // 2)
    return math.floor(base + max(melee, rng, mage))


async def fetch_player(http: OSRSHttp, rsn: str, mode: GameMode = "normal") -> PlayerStats:
    rsn = rsn.strip()
    if not rsn or len(rsn) > 12:
        raise PlayerNotFoundError(rsn, mode)
    url = BASE_URL.format(path=MODE_PATHS[mode])
    data = await http.get_json(
        url,
        params={"player": rsn},
        ttl=HISCORES_TTL,
        not_found=lambda: PlayerNotFoundError(rsn, mode),
    )
    return parse_hiscores(rsn, mode, data)

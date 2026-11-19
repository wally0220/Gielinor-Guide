"""Experience math. Pure functions, no network.

OSRS XP for level L is:

    xp(L) = floor( (1/4) * sum_{n=1}^{L-1} floor(n + 300 * 2^(n/7)) )

Levels go to 99 (13,034,431 XP); "virtual" levels continue to 126 (~188.9M).
Skill XP is capped at 200,000,000.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from osrs_assistant.core.errors import InvalidInputError

MAX_LEVEL = 99
MAX_VIRTUAL_LEVEL = 126
MAX_XP = 200_000_000


def _build_table() -> tuple[int, ...]:
    table = [0, 0]  # index 0 unused, level 1 = 0 XP
    points = 0
    for level in range(1, MAX_VIRTUAL_LEVEL):
        points += math.floor(level + 300 * 2 ** (level / 7))
        table.append(points // 4)
    return tuple(table)


# XP_TABLE[level] = XP required to reach that level, for level 1..126.
XP_TABLE: tuple[int, ...] = _build_table()


def xp_for_level(level: int) -> int:
    if not 1 <= level <= MAX_VIRTUAL_LEVEL:
        raise InvalidInputError(f"Level must be between 1 and {MAX_VIRTUAL_LEVEL}, got {level}.")
    return XP_TABLE[level]


def level_for_xp(xp: int, *, virtual: bool = False) -> int:
    if xp < 0:
        raise InvalidInputError(f"XP can't be negative, got {xp}.")
    cap = MAX_VIRTUAL_LEVEL if virtual else MAX_LEVEL
    level = 1
    while level < cap and XP_TABLE[level + 1] <= xp:
        level += 1
    return level


@dataclass(frozen=True)
class Method:
    """A way to train: e.g. Method("Lvl-5 Enchant", 78) or Method("Gold bar (gauntlets)", 56.2)."""

    name: str
    xp_each: float


@dataclass(frozen=True)
class MethodResult:
    name: str
    xp_each: float
    actions: int


@dataclass(frozen=True)
class XpPlan:
    current_xp: int
    current_level: int
    target_level: int
    target_xp: int
    xp_remaining: int
    percent_complete: float
    """Progress from the start of the current level to the target."""
    methods: tuple[MethodResult, ...]


def actions_needed(xp_remaining: int, xp_each: float) -> int:
    if xp_each <= 0:
        raise InvalidInputError(f"XP per action must be positive, got {xp_each}.")
    if xp_remaining <= 0:
        return 0
    # Round to avoid float noise like 56.2 * n = 636293.9999.
    return math.ceil(round(xp_remaining / xp_each, 9))


def plan_to_level(
    current_xp: int, target_level: int, methods: list[Method] | None = None
) -> XpPlan:
    target_xp = xp_for_level(target_level)
    current_level = level_for_xp(current_xp, virtual=True)
    remaining = max(0, target_xp - current_xp)

    start = xp_for_level(current_level)
    span = target_xp - start
    percent = 100.0 if remaining == 0 or span <= 0 else round(100 * (current_xp - start) / span, 1)

    results = tuple(
        MethodResult(m.name, m.xp_each, actions_needed(remaining, m.xp_each)) for m in methods or []
    )
    return XpPlan(
        current_xp=current_xp,
        current_level=current_level,
        target_level=target_level,
        target_xp=target_xp,
        xp_remaining=remaining,
        percent_complete=percent,
        methods=results,
    )

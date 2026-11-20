"""Training suggestions: which methods fit this account, and roughly how long they'll take.

Inputs are combined from every data source we have:
  * hiscores       -> exact XP, other skill levels and combat level (required)
  * WikiSync       -> quests and diaries, for unlock requirements (optional)
  * RuneLite files -> items owned, for requirements and "you already have N gold ore" (optional)

When an optional source is missing, requirements it would answer are reported as "unknown"
rather than guessed. Rates come from the curated `data/training_methods.toml`, verified
against the OSRS Wiki's training guides.
"""

from __future__ import annotations

import math
import tomllib
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from gielinor_guide.core import xp as xpmath
from gielinor_guide.core.errors import InvalidInputError, OSRSError
from gielinor_guide.core.hiscores import PlayerStats
from gielinor_guide.core.runelite_export import OwnedItems
from gielinor_guide.core.skills import normalize_skill
from gielinor_guide.core.wikisync import AccountProgress, QuestState

Intensity = Literal["afk", "low", "medium", "high"]
Cost = Literal["profit", "cheap", "moderate", "expensive"]
Preference = Literal["balanced", "fast", "afk", "profit"]
Status = Literal["met", "missing", "unknown"]

INTENSITY_ORDER: dict[str, int] = {"afk": 0, "low": 1, "medium": 2, "high": 3}
COST_ORDER: dict[str, int] = {"profit": 0, "cheap": 1, "moderate": 2, "expensive": 3}
# How much a "balanced" ranking discounts click-heavy methods.
BALANCED_WEIGHT: dict[str, float] = {"afk": 1.0, "low": 0.95, "medium": 0.85, "high": 0.7}


# ---------- Dataset schema (validated on load) ----------


class Requirements(BaseModel):
    quests: list[str] = []
    quests_started: list[str] = Field(default=[], description="Started or completed is enough")
    diaries: list[str] = []
    items: list[list[str]] = Field(default=[], description="Each inner list is an any-of group")
    skills: dict[str, int] = {}
    combat_level: int | None = Field(default=None, ge=3, le=126)


class Boost(BaseModel):
    name: str
    kind: Literal["item", "diary", "quest"]
    effect: str
    items: list[str] = []
    any_items: list[str] = []


class Tier(BaseModel):
    level: int
    crop: str
    xp_per_patch: float = Field(gt=0)


class MethodData(BaseModel):
    id: str
    skill: str
    name: str
    min_level: int = Field(ge=1, le=99)
    kind: Literal["hourly", "runs"] = "hourly"
    xp_each: float | None = Field(None, gt=0)
    xp_per_hour: tuple[int, int] | None = None
    rates: list[tuple[int, int]] = []
    rates_range: list[tuple[int, int, int]] = []
    casual_factor: float = Field(0.8, gt=0, le=1)
    intensity: Intensity
    cost: Cost
    recipes: list[dict[str, int]] = []
    tools: list[list[str]] = []
    requires: Requirements = Requirements()
    boosts: list[Boost] = []
    approximate: bool = False
    variance: str = ""
    notes: str = ""
    source: str
    patches: int | None = None
    minutes_per_run: float | None = None
    runs_per_day: float | None = None
    tiers: list[Tier] = []

    @model_validator(mode="after")
    def _check(self) -> MethodData:
        try:
            self.skill = normalize_skill(self.skill)
        except OSRSError as exc:
            raise ValueError(str(exc)) from exc
        if not self.source.startswith("https://"):
            raise ValueError(f"{self.id}: every method needs an https source")
        if self.recipes and self.xp_each is None:
            raise ValueError(f"{self.id}: `recipes` needs xp_each to count actions")
        if self.approximate and not self.variance:
            raise ValueError(f"{self.id}: approximate methods must explain their `variance`")

        if self.kind == "runs":
            if not (self.tiers and self.patches and self.minutes_per_run and self.runs_per_day):
                raise ValueError(f"{self.id}: run methods need tiers, patches, minutes, runs/day")
            if self.tiers[0].level != self.min_level:
                raise ValueError(f"{self.id}: first tier must match min_level")
            if [t.level for t in self.tiers] != sorted(t.level for t in self.tiers):
                raise ValueError(f"{self.id}: tiers must be in level order")
            return self

        given = [bool(self.xp_per_hour), bool(self.rates), bool(self.rates_range)]
        if sum(given) != 1:
            raise ValueError(f"{self.id}: give exactly one of xp_per_hour, rates, rates_range")
        table = self.rate_table()
        levels = [lvl for lvl, _, _ in table]
        if levels[0] != self.min_level or levels != sorted(set(levels)):
            raise ValueError(f"{self.id}: rate levels must start at min_level and increase")
        for lvl, low, high in table:
            if not 0 < low <= high <= 1_000_000:
                raise ValueError(f"{self.id}: bad rate at level {lvl}: {low}-{high}")
        return self

    def rate_table(self) -> list[tuple[int, int, int]]:
        """[(level, low, high), ...] from whichever rate field the entry uses."""
        if self.xp_per_hour:
            return [(self.min_level, *self.xp_per_hour)]
        if self.rates:
            return [(lvl, round(xp * self.casual_factor), xp) for lvl, xp in self.rates]
        return [tuple(r) for r in self.rates_range]  # type: ignore[misc]

    def rate_at(self, level: int) -> tuple[int, int]:
        table = self.rate_table()
        i = max(0, bisect_right([lvl for lvl, _, _ in table], level) - 1)
        return table[i][1], table[i][2]


class Dataset(BaseModel):
    method: list[MethodData]

    @model_validator(mode="after")
    def _unique_ids(self) -> Dataset:
        ids = [m.id for m in self.method]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate method ids")
        return self


@cache
def load_methods() -> tuple[MethodData, ...]:
    text = files("gielinor_guide.core.data").joinpath("training_methods.toml").read_text("utf-8")
    return tuple(Dataset.model_validate(tomllib.loads(text)).method)


def skills_with_methods() -> list[str]:
    return sorted({m.skill for m in load_methods()})


# ---------- Results ----------


@dataclass(frozen=True)
class Check:
    kind: str
    name: str
    status: Status
    detail: str = ""

    @property
    def blocking(self) -> bool:
        """Tools are cheap to buy, so a missing tool is reported but never locks a method."""
        return self.kind != "tool"


@dataclass(frozen=True)
class Ingredient:
    item: str
    have: int | None
    needed: int


@dataclass(frozen=True)
class RecipePlan:
    ingredients: tuple[Ingredient, ...]
    actions_from_owned: int | None
    """How many actions this recipe can make from owned items (after earlier recipes)."""


@dataclass
class MethodPlan:
    method: MethodData
    start_level: int
    """Level the plan starts from: current level, or the unlock level for later methods."""
    xp_remaining: int
    hours: tuple[float, float]
    rate_now: tuple[int, int] = (0, 0)
    actions_needed: int | None = None
    runs_needed: int | None = None
    days: float | None = None
    requirements: list[Check] = field(default_factory=list)
    boosts: list[Check] = field(default_factory=list)
    recipes: list[RecipePlan] = field(default_factory=list)
    owned_covers_percent: float | None = None
    """Share of the XP gap covered by consumables already owned (only with a bank export)."""

    @property
    def available(self) -> bool:
        return all(c.status != "missing" for c in self.requirements if c.blocking)

    @property
    def missing(self) -> list[Check]:
        return [c for c in self.requirements if c.status == "missing" and c.blocking]


@dataclass(frozen=True)
class RouteStep:
    method: MethodData
    from_level: int
    to_level: int
    xp: int
    hours: tuple[float, float]


@dataclass(frozen=True)
class CombinedRuns:
    """All available run methods done together each day (e.g. tree + fruit tree runs)."""

    methods: tuple[str, ...]
    days: int
    active_hours: float


@dataclass
class TrainingPlan:
    skill: str
    current_level: int
    current_xp: int
    target_level: int
    target_xp: int
    xp_remaining: int
    preference: Preference
    available: list[MethodPlan]
    unlocks_later: list[MethodPlan]
    locked: list[MethodPlan]
    route: list[RouteStep]
    route_gaps: list[tuple[int, int]]
    """Level ranges no usable hourly method covers (e.g. Farming before Tithe Farm)."""
    combined_runs: CombinedRuns | None
    used_wikisync: bool
    used_items: bool

    @property
    def route_hours(self) -> tuple[float, float]:
        return (sum(s.hours[0] for s in self.route), sum(s.hours[1] for s in self.route))

    @property
    def approximate(self) -> bool:
        plans = [*self.available, *self.unlocks_later]
        return any(p.method.approximate for p in plans) or any(
            s.method.approximate for s in self.route
        )


# ---------- Requirement checks ----------


class _Context:
    def __init__(
        self,
        stats: PlayerStats,
        progress: AccountProgress | None,
        owned: OwnedItems | None,
    ) -> None:
        self.stats, self.progress, self.owned = stats, progress, owned

    def quest(self, name: str, *, started_ok: bool = False) -> Check:
        kind = "quest_started" if started_ok else "quest"
        if self.progress is None:
            return Check(kind, name, "unknown", "needs WikiSync")
        hit = self.progress.find_quest(name)
        if hit is None:
            return Check(kind, name, "unknown", "quest not found in WikiSync data")
        _, state = hit
        if state == QuestState.COMPLETED or (started_ok and state == QuestState.IN_PROGRESS):
            return Check(kind, name, "met", "started" if state == QuestState.IN_PROGRESS else "")
        return Check(kind, name, "missing", state.name.lower().replace("_", " "))

    def diary(self, label: str) -> Check:
        region, _, tier = label.rpartition(" ")
        if self.progress is None:
            return Check("diary", label, "unknown", "needs WikiSync")
        d = self.progress.diary(region, tier)
        if d is None:
            return Check("diary", label, "unknown", "diary not found in WikiSync data")
        if d.complete:
            return Check("diary", label, "met")
        return Check("diary", label, "missing", f"{d.tasks_done}/{d.tasks_total} tasks")

    def any_item(self, options: list[str], label: str | None = None, kind: str = "item") -> Check:
        name = label or " / ".join(options)
        if self.owned is None:
            return Check(kind, name, "unknown", "needs a RuneLite bank export")
        for option in options:
            if self.owned.has(option):
                where = ", ".join(self.owned.where(option))
                return Check(kind, name, "met", f"{option} ({where})")
        return Check(kind, name, "missing")

    def all_items(self, items: list[str], label: str) -> Check:
        if self.owned is None:
            return Check("item", label, "unknown", "needs a RuneLite bank export")
        lacking = [i for i in items if not self.owned.has(i)]
        if not lacking:
            return Check("item", label, "met")
        return Check("item", label, "missing", "missing " + ", ".join(lacking))

    def skill(self, skill: str, level: int) -> Check:
        name = f"{level} {skill}"
        have = self.stats.level(skill)
        if have is None:
            return Check("skill", name, "unknown", "unranked on the hiscores")
        return Check("skill", name, "met" if have >= level else "missing", f"have {have}")

    def combat(self, level: int) -> Check:
        name = f"{level} combat"
        have = self.stats.combat_level
        if have is None:
            return Check("combat", name, "unknown", "a combat skill is unranked")
        return Check("combat", name, "met" if have >= level else "missing", f"have {have}")

    def requirements(self, m: MethodData) -> list[Check]:
        r = m.requires
        checks = [
            *(self.quest(q) for q in r.quests),
            *(self.quest(q, started_ok=True) for q in r.quests_started),
            *(self.diary(d) for d in r.diaries),
            *(self.any_item(group) for group in r.items),
            *(self.skill(normalize_skill(s), lvl) for s, lvl in r.skills.items()),
        ]
        if r.combat_level is not None:
            checks.append(self.combat(r.combat_level))
        checks.extend(self.any_item(group, kind="tool") for group in m.tools)
        return checks

    def boosts(self, m: MethodData) -> list[Check]:
        out: list[Check] = []
        for b in m.boosts:
            if b.kind == "quest":
                check = self.quest(b.name)
            elif b.kind == "diary":
                check = self.diary(b.name)
            elif b.items:
                check = self.all_items(b.items, b.name)
            else:
                check = self.any_item(b.any_items or [b.name], b.name)
            out.append(Check(check.kind, b.name, check.status, b.effect))
        return out


# ---------- Planning ----------


def _hourly_hours(m: MethodData, start_xp: int, end_xp: int) -> tuple[float, float]:
    """Hours for [start_xp, end_xp), switching rate at every level in the rate table."""
    boundaries = [lvl for lvl, _, _ in m.rate_table()]
    xp, fast, slow = start_xp, 0.0, 0.0
    while xp < end_xp:
        level = xpmath.level_for_xp(xp, virtual=True)
        low, high = m.rate_at(level)
        nxt = next((b for b in boundaries if b > level), None)
        seg_end = min(end_xp, xpmath.xp_for_level(nxt)) if nxt else end_xp
        fast += (seg_end - xp) / high
        slow += (seg_end - xp) / low
        xp = seg_end
    return (fast, slow)


def _simulate_runs(m: MethodData, start_xp: int, target_xp: int) -> int:
    """Count runs, re-picking the best crop as levels rise. Returns runs needed."""
    assert m.patches is not None
    xp, runs = start_xp, 0
    while xp < target_xp and runs < 100_000:
        level = xpmath.level_for_xp(xp)
        tier = max((t for t in m.tiers if t.level <= level), key=lambda t: t.level)
        xp += math.floor(tier.xp_per_patch * m.patches)
        runs += 1
    return runs


def _plan_recipes(
    m: MethodData, owned: OwnedItems | None, actions: int
) -> tuple[list[RecipePlan], int | None]:
    """Greedily use owned ingredients across alternative recipes (unf potions first, etc.)."""
    if owned is None:
        plans = [
            RecipePlan(tuple(Ingredient(i, None, actions * q) for i, q in recipe.items()), None)
            for recipe in m.recipes
        ]
        return plans, None
    left = {i: owned.count(i) for recipe in m.recipes for i in recipe}
    total = 0
    plans = []
    for recipe in m.recipes:
        can = min(left[i] // q for i, q in recipe.items())
        can = min(can, max(0, actions - total))
        for i, q in recipe.items():
            left[i] -= can * q
        total += can
        plans.append(
            RecipePlan(
                tuple(Ingredient(i, owned.count(i), actions * q) for i, q in recipe.items()), can
            )
        )
    return plans, total


def _plan_method(
    m: MethodData, ctx: _Context, start_xp: int, start_level: int, target_xp: int
) -> MethodPlan:
    remaining = max(0, target_xp - start_xp)
    plan = MethodPlan(method=m, start_level=start_level, xp_remaining=remaining, hours=(0.0, 0.0))

    if m.kind == "runs":
        assert m.minutes_per_run is not None and m.runs_per_day is not None
        runs = _simulate_runs(m, start_xp, target_xp)
        active = runs * m.minutes_per_run / 60
        plan.runs_needed = runs
        plan.days = runs / m.runs_per_day
        plan.hours = (active, active)
    else:
        plan.hours = _hourly_hours(m, start_xp, target_xp)
        plan.rate_now = m.rate_at(start_level)

    if m.xp_each:
        plan.actions_needed = xpmath.actions_needed(remaining, m.xp_each)
        if m.recipes:
            plan.recipes, owned_actions = _plan_recipes(m, ctx.owned, plan.actions_needed)
            if owned_actions is not None and remaining > 0:
                plan.owned_covers_percent = round(
                    min(100.0, 100 * owned_actions * m.xp_each / remaining), 1
                )

    plan.requirements = ctx.requirements(m)
    plan.boosts = ctx.boosts(m)
    return plan


def _sort_key(plan: MethodPlan, preference: Preference) -> tuple[float, ...]:
    m = plan.method
    hours_mid = sum(plan.hours) / 2 if plan.hours[1] > 0 else 0.0
    if m.kind == "runs":
        assert plan.days is not None
        # Runs are cheap in attention but slow in real time; rank them by days.
        hours_mid = max(hours_mid, plan.days * 2)
    owned_bonus = -(plan.owned_covers_percent or 0.0) / 100
    if preference == "fast":
        return (hours_mid, owned_bonus)
    if preference == "afk":
        return (INTENSITY_ORDER[m.intensity], hours_mid)
    if preference == "profit":
        return (COST_ORDER[m.cost], hours_mid)
    weight = BALANCED_WEIGHT[m.intensity]
    return (hours_mid / weight * (1 + owned_bonus * 0.5), COST_ORDER[m.cost])


def _usable(m: MethodData, ctx: _Context) -> bool:
    return all(c.status != "missing" for c in ctx.requirements(m) if c.blocking)


def _build_route(
    methods: list[MethodData],
    ctx: _Context,
    current_xp: int,
    current_level: int,
    target: int,
    preference: Preference,
) -> list[RouteStep]:
    """Greedy route over hourly methods: re-pick the best method at every rate boundary."""
    if target <= current_level or current_xp >= xpmath.xp_for_level(target):
        return []
    hourly = [m for m in methods if m.kind == "hourly" and _usable(m, ctx)]
    cuts = {current_level, target}
    for m in hourly:
        cuts.update(lvl for lvl, _, _ in m.rate_table() if current_level < lvl < target)
    steps: list[RouteStep] = []
    for lo, hi in pairwise(sorted(cuts)):
        start_xp = current_xp if lo == current_level else xpmath.xp_for_level(lo)
        end_xp = xpmath.xp_for_level(hi)
        candidates = [m for m in hourly if m.min_level <= lo]
        if not candidates or end_xp <= start_xp:
            continue
        plans = [_plan_method(m, ctx, start_xp, lo, end_xp) for m in candidates]
        best = min(plans, key=lambda p: _sort_key(p, preference))
        if steps and steps[-1].method.id == best.method.id and steps[-1].to_level == lo:
            prev = steps.pop()
            hours = (prev.hours[0] + best.hours[0], prev.hours[1] + best.hours[1])
            xp_total = prev.xp + best.xp_remaining
            steps.append(RouteStep(best.method, prev.from_level, hi, xp_total, hours))
        else:
            steps.append(RouteStep(best.method, lo, hi, best.xp_remaining, best.hours))
    return steps


def _combined_runs(
    methods: list[MethodData], ctx: _Context, current_xp: int, target_xp: int
) -> CombinedRuns | None:
    runs = [m for m in methods if m.kind == "runs" and _usable(m, ctx)]
    if len(runs) < 2 or current_xp >= target_xp:
        return None
    xp, days, minutes = current_xp, 0, 0.0
    while xp < target_xp and days < 100_000:
        level = xpmath.level_for_xp(xp)
        for m in runs:
            assert m.patches is not None and m.minutes_per_run is not None
            tiers = [t for t in m.tiers if t.level <= level]
            if tiers:
                xp += math.floor(max(tiers, key=lambda t: t.level).xp_per_patch * m.patches)
                minutes += m.minutes_per_run
        days += 1
    return CombinedRuns(tuple(m.name for m in runs), days, round(minutes / 60, 1))


def plan_training(
    skill: str,
    stats: PlayerStats,
    *,
    target_level: int | None = None,
    progress: AccountProgress | None = None,
    owned: OwnedItems | None = None,
    preference: Preference = "balanced",
) -> TrainingPlan:
    canonical = normalize_skill(skill)
    stat = stats.skills.get(canonical)
    if stat is None or stat.xp is None or stat.level is None:
        raise InvalidInputError(f"{canonical} is unranked on the hiscores, so exact XP is unknown.")

    current_xp = stat.xp
    current_level = xpmath.level_for_xp(current_xp)
    target = target_level or min(current_level + 1, xpmath.MAX_LEVEL)
    if not 2 <= target <= xpmath.MAX_LEVEL:
        raise InvalidInputError(f"Target level must be between 2 and {xpmath.MAX_LEVEL}.")
    target_xp = xpmath.xp_for_level(target)

    methods = [m for m in load_methods() if m.skill == canonical]
    if not methods:
        known = ", ".join(skills_with_methods())
        raise InvalidInputError(
            f"No curated training methods for {canonical} yet. Skills covered so far: {known}."
        )

    ctx = _Context(stats, progress, owned)
    available: list[MethodPlan] = []
    later: list[MethodPlan] = []
    locked: list[MethodPlan] = []
    for m in methods:
        if m.min_level <= current_level:
            plan = _plan_method(m, ctx, current_xp, current_level, target_xp)
        elif m.min_level < target:
            plan = _plan_method(m, ctx, xpmath.xp_for_level(m.min_level), m.min_level, target_xp)
            later.append(plan)
            continue
        else:
            continue
        (available if plan.available else locked).append(plan)

    available.sort(key=lambda p: _sort_key(p, preference))
    later.sort(key=lambda p: p.start_level)
    route = _build_route(methods, ctx, current_xp, current_level, target, preference)
    combined = _combined_runs(methods, ctx, current_xp, target_xp)
    gaps: list[tuple[int, int]] = []
    if current_xp < target_xp:
        cursor = current_level
        for step in route:
            if step.from_level > cursor:
                gaps.append((cursor, step.from_level))
            cursor = step.to_level
        if cursor < target:
            gaps.append((cursor, target))

    return TrainingPlan(
        skill=canonical,
        current_level=current_level,
        current_xp=current_xp,
        target_level=target,
        target_xp=target_xp,
        xp_remaining=max(0, target_xp - current_xp),
        preference=preference,
        available=available,
        unlocks_later=later,
        locked=locked,
        route=route,
        route_gaps=gaps,
        combined_runs=combined,
        used_wikisync=progress is not None,
        used_items=owned is not None,
    )

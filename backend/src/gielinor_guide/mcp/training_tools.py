"""suggest_training: method recommendations + time estimates from all account data sources."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from gielinor_guide.core import xp as xpmath
from gielinor_guide.core.errors import OSRSError
from gielinor_guide.core.hiscores import GameMode, fetch_player
from gielinor_guide.core.http import OSRSHttp
from gielinor_guide.core.runelite_export import OwnedItems, load_owned_items
from gielinor_guide.core.training import (
    Check,
    MethodPlan,
    Preference,
    plan_training,
    skills_with_methods,
)
from gielinor_guide.core.wikisync import AccountProgress, fetch_progress

READ_ONLY_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)

SUGGEST_TRAINING_DESCRIPTION = """\
Recommend training methods for a skill and estimate how long the goal takes.

Starts from the player's exact XP (hiscores), checks quest/diary unlocks (WikiSync)
and owned items (RuneLite export): e.g. "the gold ore you own covers this
whole goal". Returns a level-by-level route plus every usable method with an hour range.

Args:
    skill: e.g. "thieving", "smithing", "herb".
    target_level: Goal level. Omit for the next level.
    rsn: Omit for the configured default account.
    prefer: "balanced" (default), "fast", "afk" (low attention) or "profit".
    use_account_data: Set false to skip WikiSync and the bank export.
    max_methods: How many usable methods to return.

Curated skills so far: {covered}. Hours are estimates; mention that they're ranges.
"""

APPROXIMATE_NOTICE = {
    "Slayer": (
        "Slayer rates are rough approximations: Slayer XP equals the HP of what you kill, so "
        "it varies a lot with your Slayer master, task assignments, gear and cannon/burst use. "
        "Treat these hours as a ballpark, not a plan."
    ),
    "default": "Some rates here are rough approximations; see each method's variance.",
}

DISCLAIMER = (
    "Hours are ranges from community guides (casual to focused play), not promises. "
    "They don't account for banking breaks, gear differences or bad luck."
)


# ---------- Output schemas ----------


class CheckOut(BaseModel):
    name: str
    status: str = Field(description="met | missing | unknown")
    detail: str = ""


class IngredientOut(BaseModel):
    item: str
    have: int | None = Field(description="None if no RuneLite export was available")
    needed: int = Field(description="Needed if this recipe alone covered the whole goal")


class RecipeOut(BaseModel):
    ingredients: list[IngredientOut]
    actions_from_owned: int | None = Field(
        description="Actions you can make from owned items with this recipe (after earlier ones)"
    )


class MethodOut(BaseModel):
    id: str
    name: str
    from_level: int
    intensity: str
    cost: str
    xp_per_hour: str | None = Field(description="Rate range at from_level")
    approximate: bool = Field(description="True for rough estimates (see variance)")
    variance: str = ""
    hours: str = Field(description='e.g. "0.5-0.9 h" (active play time)')
    hours_low: float
    hours_high: float
    actions_needed: int | None
    runs_needed: int | None = None
    days: float | None = Field(None, description="Real days for run-based methods")
    owned_covers_percent: float | None = Field(
        None, description="Share of the XP gap your owned materials already cover"
    )
    recipes: list[RecipeOut] = Field(description="Alternative ingredient sets, in order of use")
    requirements: list[CheckOut]
    boosts: list[CheckOut]
    notes: str
    source: str


class LockedOut(BaseModel):
    id: str
    name: str
    missing: list[str]


class RouteStepOut(BaseModel):
    method: str
    from_level: int
    to_level: int
    xp: int
    hours: str


class CombinedRunsOut(BaseModel):
    methods: list[str]
    days: int
    active_hours: float


class TrainingOut(BaseModel):
    skill: str
    current_level: int
    current_xp: int
    target_level: int
    xp_remaining: int
    preference: str
    route: list[RouteStepOut] = Field(description="Best method per level range, in order")
    route_hours: str | None
    route_gaps: list[str] = Field(description="Level ranges no usable hourly method covers")
    combined_runs: CombinedRunsOut | None = Field(
        None, description="Doing every available run method daily (e.g. tree + fruit trees)"
    )
    methods: list[MethodOut] = Field(description="Usable now, best first for `prefer`")
    unlocks_later: list[MethodOut] = Field(description="Become usable before the target level")
    locked: list[LockedOut] = Field(description="Level OK but a requirement is missing")
    data_sources: dict[str, str]
    notes: list[str]
    disclaimer: str


# ---------- Conversion helpers ----------


def _fmt_hours(low: float, high: float) -> str:
    def f(h: float) -> str:
        return f"{h:.1f}" if h < 10 else f"{h:.0f}"

    return f"{f(low)} h" if f(low) == f(high) else f"{f(low)}-{f(high)} h"


def _fmt_rate(rate: tuple[int, int]) -> str | None:
    if rate == (0, 0):
        return None
    return f"{rate[0] // 1000}k-{rate[1] // 1000}k"


def _check(c: Check) -> CheckOut:
    return CheckOut(name=c.name, status=c.status, detail=c.detail)


def _method(p: MethodPlan) -> MethodOut:
    m = p.method
    return MethodOut(
        id=m.id,
        name=m.name,
        from_level=p.start_level,
        intensity=m.intensity,
        cost=m.cost,
        xp_per_hour=_fmt_rate(p.rate_now),
        approximate=m.approximate,
        variance=m.variance,
        hours=_fmt_hours(*p.hours),
        hours_low=round(p.hours[0], 2),
        hours_high=round(p.hours[1], 2),
        actions_needed=p.actions_needed,
        runs_needed=p.runs_needed,
        days=round(p.days, 1) if p.days is not None else None,
        owned_covers_percent=p.owned_covers_percent,
        recipes=[
            RecipeOut(
                ingredients=[
                    IngredientOut(item=i.item, have=i.have, needed=i.needed) for i in r.ingredients
                ],
                actions_from_owned=r.actions_from_owned,
            )
            for r in p.recipes
        ],
        requirements=[_check(c) for c in p.requirements],
        boosts=[_check(c) for c in p.boosts],
        notes=m.notes,
        source=m.source,
    )


# ---------- Registration ----------


def register_training_tools(
    server: MCPServer,
    *,
    client: OSRSHttp,
    resolve_rsn: Callable[[str | None], str],
) -> None:
    description = SUGGEST_TRAINING_DESCRIPTION.format(covered=", ".join(skills_with_methods()))

    @server.tool(annotations=READ_ONLY_REMOTE, description=description)
    async def suggest_training(
        skill: str,
        target_level: Annotated[int | None, Field(ge=2, le=xpmath.MAX_LEVEL)] = None,
        rsn: str | None = None,
        prefer: Preference = "balanced",
        use_account_data: bool = True,
        max_methods: Annotated[int, Field(ge=1, le=10)] = 4,
        mode: GameMode = "normal",
    ) -> TrainingOut:
        """See SUGGEST_TRAINING_DESCRIPTION."""
        name = resolve_rsn(rsn)
        try:
            stats = await fetch_player(client, name, mode)
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

        sources: dict[str, str] = {"hiscores": "ok"}
        notes: list[str] = []
        progress: AccountProgress | None = None
        owned: OwnedItems | None = None
        if use_account_data:
            try:
                progress = await fetch_progress(client, name)
                sources["wikisync"] = "ok"
            except OSRSError as exc:
                sources["wikisync"] = f"unavailable: {exc}"
                notes.append("Quest/diary requirements are 'unknown' without WikiSync data.")
            try:
                owned = load_owned_items(client.settings.export_dir, rsn=name)
                sources["runelite_export"] = "ok"
            except OSRSError as exc:
                sources["runelite_export"] = f"unavailable: {exc}"
                notes.append("Item checks are 'unknown' without a RuneLite bank export.")
        else:
            sources["wikisync"] = sources["runelite_export"] = "skipped"

        try:
            plan = plan_training(
                skill,
                stats,
                target_level=target_level,
                progress=progress,
                owned=owned,
                preference=prefer,
            )
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

        if plan.xp_remaining == 0:
            notes.append(f"Already at or past level {plan.target_level} {plan.skill}.")
        for p in plan.available[:1]:
            if p.owned_covers_percent and p.owned_covers_percent >= 100:
                notes.append(
                    f"Materials you already own cover this whole goal with {p.method.name}."
                )
        # Boost tips for every method the route actually uses (not just the top-ranked one).
        by_id = {p.method.id: p for p in [*plan.available, *plan.unlocks_later]}
        for step_id in dict.fromkeys(step.method.id for step in plan.route):
            route_plan = by_id.get(step_id)
            if route_plan is None:
                continue
            missing_boosts = [b for b in route_plan.boosts if b.status == "missing"]
            for b in missing_boosts:
                notes.append(f"{route_plan.method.name}: {b.name} would help ({b.detail}).")
        if plan.combined_runs and plan.route_gaps:
            notes.append("Run-based methods cover the gaps; see combined_runs.")
        missing_tools = sorted(
            {
                c.name
                for p in plan.available[:max_methods]
                for c in p.requirements
                if c.kind == "tool" and c.status == "missing"
            }
        )
        if missing_tools:
            notes.append(f"Bring or buy: {', '.join(missing_tools)}.")
        if plan.approximate:
            notes.insert(
                0,
                APPROXIMATE_NOTICE.get(plan.skill, APPROXIMATE_NOTICE["default"]),
            )

        route_hours = plan.route_hours
        return TrainingOut(
            skill=plan.skill,
            current_level=plan.current_level,
            current_xp=plan.current_xp,
            target_level=plan.target_level,
            xp_remaining=plan.xp_remaining,
            preference=plan.preference,
            route=[
                RouteStepOut(
                    method=s.method.name,
                    from_level=s.from_level,
                    to_level=s.to_level,
                    xp=s.xp,
                    hours=_fmt_hours(*s.hours),
                )
                for s in plan.route
            ],
            route_hours=_fmt_hours(*route_hours) if plan.route else None,
            route_gaps=[f"{a}-{b}" for a, b in plan.route_gaps],
            combined_runs=(
                CombinedRunsOut(
                    methods=list(plan.combined_runs.methods),
                    days=plan.combined_runs.days,
                    active_hours=plan.combined_runs.active_hours,
                )
                if plan.combined_runs
                else None
            ),
            methods=[_method(p) for p in plan.available[:max_methods]],
            unlocks_later=[_method(p) for p in plan.unlocks_later],
            locked=[
                LockedOut(
                    id=p.method.id,
                    name=p.method.name,
                    missing=[f"{c.name} ({c.detail})" if c.detail else c.name for c in p.missing],
                )
                for p in plan.locked
            ],
            data_sources=sources,
            notes=notes,
            disclaimer=DISCLAIMER,
        )

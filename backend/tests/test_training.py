from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gielinor_guide.core import training
from gielinor_guide.core.errors import InvalidInputError
from gielinor_guide.core.hiscores import PlayerStats, parse_hiscores
from gielinor_guide.core.runelite_export import ContainerExport, Item, OwnedItems
from gielinor_guide.core.training import MethodData, plan_training
from gielinor_guide.core.wikisync import AccountProgress, parse_wikisync
from gielinor_guide.core.xp import level_for_xp, xp_for_level


@pytest.fixture
def stats_for(hiscores_sample: dict[str, Any]) -> Any:
    def build(**xp_by_skill: int) -> PlayerStats:
        data = copy.deepcopy(hiscores_sample)
        for entry in data["skills"]:
            if entry["name"] in xp_by_skill:
                entry["xp"] = xp_by_skill[entry["name"]]
                entry["level"] = level_for_xp(entry["xp"])
        return parse_hiscores("SamplePlayer", "normal", data)

    return build


@pytest.fixture
def progress_with(wikisync_sample: dict[str, Any]) -> Any:
    def build(**quests: int) -> AccountProgress:
        data = copy.deepcopy(wikisync_sample)
        data["quests"].update(quests)
        return parse_wikisync("SamplePlayer", data)

    return build


def owned(items: dict[str, int] | None = None, /, **by_underscored_name: int) -> OwnedItems:
    """owned({"Ranarr potion (unf)": 5}) or owned(Gold_ore=5)."""
    all_items = {
        **(items or {}),
        **{k.replace("_", " "): v for k, v in by_underscored_name.items()},
    }
    export = ContainerExport(
        container="bank",
        path=Path("/tmp/container_bank.json"),
        exported_at=datetime(2026, 11, 19, tzinfo=UTC),
        items=tuple(Item(n, name, q) for n, (name, q) in enumerate(all_items.items())),
    )
    return OwnedItems({"bank": export})


def ids(plans: list[Any]) -> list[str]:
    return [p.method.id for p in plans]


def find(plans: list[Any], method_id: str) -> Any:
    return next(p for p in plans if p.method.id == method_id)


def method(method_id: str) -> MethodData:
    return next(m for m in training.load_methods() if m.id == method_id)


# ---------- Rates: per-level tables ----------


def test_per_level_rates_switch_inside_a_plan() -> None:
    knights = method("ardougne_knights")
    assert knights.rate_at(55) == (68_800, 86_000)  # casual_factor 0.8
    assert knights.rate_at(59) == knights.rate_at(55)
    assert knights.rate_at(60) == (78_400, 98_000)
    # 55 -> 65 crosses the 60 boundary: hours must use both rates, not just the first.
    fast, slow = training._hourly_hours(knights, xp_for_level(55), xp_for_level(65))
    xp_a = xp_for_level(60) - xp_for_level(55)
    xp_b = xp_for_level(65) - xp_for_level(60)
    assert fast == pytest.approx(xp_a / 86_000 + xp_b / 98_000)
    assert slow == pytest.approx(xp_a / 68_800 + xp_b / 78_400)


def test_explicit_low_high_table() -> None:
    sal = method("red_salamanders")
    assert sal.rate_at(59) == (55_000, 60_000)
    assert sal.rate_at(66) == (80_000, 85_000)
    assert sal.rate_at(99) == (100_000, 105_000)


# ---------- Smithing: owned materials ----------


def test_owned_gold_ore_covers_goal(stats_for: Any) -> None:
    stats = stats_for(Smithing=557_771)  # level 67
    items = owned(Goldsmith_gauntlets=1, Gold_ore=5_000)
    plan = plan_training("smithing", stats, target_level=70, owned=items)
    assert plan.xp_remaining == 179_856
    bf = find(plan.available, "blast_furnace_gold")
    assert bf.actions_needed == 3_201
    assert bf.owned_covers_percent == 100.0
    assert bf.recipes[0].actions_from_owned == 3_201
    assert plan.route[0].method.id == "blast_furnace_gold"


def test_partial_material_coverage(stats_for: Any) -> None:
    items = owned(Smithing_cape=1, Gold_ore=1_600)
    plan = plan_training("smithing", stats_for(Smithing=557_771), target_level=70, owned=items)
    bf = find(plan.available, "blast_furnace_gold")
    assert bf.owned_covers_percent == pytest.approx(50.0, abs=0.1)
    assert bf.requirements[0].detail.startswith("Smithing cape")


def test_missing_required_item_locks_method(stats_for: Any) -> None:
    items = owned(Gold_ore=5_000)
    plan = plan_training("smithing", stats_for(Smithing=557_771), target_level=70, owned=items)
    assert "blast_furnace_gold" in ids(plan.locked)
    assert "blast_furnace_gold" not in ids(plan.available)


def test_without_account_data_requirements_are_unknown_not_missing(stats_for: Any) -> None:
    plan = plan_training("smithing", stats_for(Smithing=557_771), target_level=70)
    assert {"blast_furnace_gold", "giants_foundry"} <= set(ids(plan.available))
    assert {c.status for p in plan.available for c in p.requirements} == {"unknown"}
    assert not plan.used_wikisync and not plan.used_items


# ---------- Herblore: alternative recipes ----------


def test_herbs_and_vials_count_as_ingredients(stats_for: Any) -> None:
    items = owned(
        {
            "Ranarr potion (unf)": 100,
            "Ranarr weed": 300,
            "Grimy ranarr weed": 200,
            "Vial of water": 450,
            "Snape grass": 1_000,
        }
    )
    plan = plan_training("herblore", stats_for(Herblore=xp_for_level(45)), owned=items)
    pots = find(plan.available, "prayer_potions")
    needed = pots.actions_needed
    # Greedy: unfinished potions first, then clean herbs (vials shared), then grimy.
    expected = []
    left = needed
    for can in (100, 300, 150):
        expected.append(min(can, left))
        left -= expected[-1]
    assert [r.actions_from_owned for r in pots.recipes] == expected
    assert pots.recipes[1].ingredients[1].item == "Vial of water"


def test_clean_herbs_alone_are_enough(stats_for: Any) -> None:
    items = owned(Ranarr_weed=10_000, Vial_of_water=10_000, Snape_grass=10_000)
    plan = plan_training("herblore", stats_for(Herblore=xp_for_level(49)), owned=items)
    pots = find(plan.available, "prayer_potions")
    assert sum(r.actions_from_owned or 0 for r in pots.recipes) == pots.actions_needed
    assert pots.owned_covers_percent == 100.0


def test_ingredients_without_vials_do_not_count(stats_for: Any) -> None:
    items = owned(Ranarr_weed=500, Snape_grass=500)
    plan = plan_training("herblore", stats_for(Herblore=xp_for_level(49)), owned=items)
    assert find(plan.available, "prayer_potions").owned_covers_percent == 0.0


# ---------- Thieving: routes, quests, tools ----------


def test_route_uses_fastest_verified_method(stats_for: Any, progress_with: Any) -> None:
    stats = stats_for(Thieving=xp_for_level(53))
    progress = progress_with(**{"Children of the Sun": 2, "The Feud": 0})
    plan = plan_training("thieving", stats, target_level=60, progress=progress)
    assert [(s.method.id, s.from_level, s.to_level) for s in plan.route] == [
        ("stealing_artefacts", 53, 60)
    ]
    assert plan.route_gaps == []
    assert "blackjacking" in ids(plan.locked)


def test_quest_started_is_enough_for_blackjacking(stats_for: Any, progress_with: Any) -> None:
    stats = stats_for(Thieving=xp_for_level(56))
    progress = progress_with(**{"The Feud": 1})
    plan = plan_training("thieving", stats, target_level=60, progress=progress)
    feud = next(c for c in find(plan.available, "blackjacking").requirements if c.kind != "tool")
    assert (feud.name, feud.status, feud.detail) == ("The Feud", "met", "started")


def test_unmet_quest_locks_and_absent_quest_is_unknown(stats_for: Any, progress_with: Any) -> None:
    stats = stats_for(Thieving=xp_for_level(56))
    progress = progress_with(**{"Children of the Sun": 0})
    plan = plan_training("thieving", stats, target_level=60, progress=progress)
    assert find(plan.locked, "wealthy_citizens").missing[0].detail == "not started"
    # The Feud isn't in the sample payload at all: unknown, so not locked.
    assert find(plan.available, "blackjacking").requirements[0].status == "unknown"


def test_missing_tool_is_reported_but_not_blocking(stats_for: Any) -> None:
    stats = stats_for(Thieving=xp_for_level(53))
    plan = plan_training("thieving", stats, target_level=60, owned=owned(Coins=1))
    artefacts = find(plan.available, "stealing_artefacts")
    tool = next(c for c in artefacts.requirements if c.kind == "tool")
    assert (tool.name, tool.status) == ("Lockpick / Hair clip", "missing")
    assert artefacts.available


def test_boosts_report_diary_and_outfit(stats_for: Any, wikisync_sample: dict[str, Any]) -> None:
    stats = stats_for(Thieving=xp_for_level(60))
    progress = parse_wikisync("SamplePlayer", wikisync_sample)  # Ardougne Medium 4/12
    items = owned(Rogue_mask=1, Rogue_top=1, Rogue_trousers=1, Rogue_gloves=1, Rogue_boots=1)
    plan = plan_training("thieving", stats, target_level=61, progress=progress, owned=items)
    boosts = {b.name: b for b in find(plan.available, "ardougne_knights").boosts}
    assert boosts["Rogue outfit"].status == "met"
    assert boosts["Ardougne Medium"].status == "missing"
    assert boosts["Dodgy necklace"].status == "missing"


# ---------- Farming: run-based methods ----------


def test_farming_runs_cover_the_whole_gap(stats_for: Any) -> None:
    plan = plan_training("farming", stats_for(Farming=xp_for_level(28)), target_level=65)
    assert {"tree_runs", "fruit_tree_runs"} <= set(ids(plan.available))
    # Tithe Farm rates are only verified from 74, so no hourly method covers 28-65.
    assert plan.route == [] and plan.route_gaps == [(28, 65)]
    assert plan.combined_runs is not None
    assert plan.combined_runs.days < find(plan.available, "tree_runs").runs_needed


def test_papaya_tier_is_used_from_57(stats_for: Any) -> None:
    fruit = method("fruit_tree_runs")
    assert [t.crop for t in fruit.tiers if 51 <= t.level <= 68] == ["Pineapple", "Papaya", "Palm"]
    # At 57 one fruit run is 4 x 6,380.4 = 25,521 XP, enough for 57 -> 58 (21,212 XP).
    plan = plan_training("farming", stats_for(Farming=xp_for_level(57)), target_level=58)
    assert find(plan.available, "fruit_tree_runs").runs_needed == 1


# ---------- Slayer: approximations ----------


def test_slayer_is_flagged_approximate_and_gated_by_combat(stats_for: Any) -> None:
    plan = plan_training("slayer", stats_for(Slayer=xp_for_level(32)), target_level=50)
    assert plan.approximate
    assert all(p.method.approximate and p.method.variance for p in plan.available)
    combat = next(
        c for c in find(plan.available, "slayer_mid_melee").requirements if c.kind == "combat"
    )
    assert (combat.status, combat.detail) == ("met", "have 101")  # sample stats: combat 101


def test_low_combat_locks_nieve_tasks(stats_for: Any) -> None:
    weak = stats_for(
        Slayer=xp_for_level(32),
        **{
            s: xp_for_level(40)
            for s in ("Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer")
        },
    )
    plan = plan_training("slayer", weak, target_level=40)
    assert "slayer_mid_melee" in ids(plan.locked)
    assert "slayer_turael" in ids(plan.available)


def test_non_slayer_plans_are_not_approximate(stats_for: Any) -> None:
    plan = plan_training("smithing", stats_for(Smithing=557_771), target_level=70)
    assert not plan.approximate


# ---------- Rankings: a synthetic dataset, so tests check the logic, not today's data ----------


def _method(**kw: Any) -> MethodData:
    base = {
        "skill": "Agility",
        "min_level": 1,
        "intensity": "medium",
        "cost": "cheap",
        "source": "https://example.com",
    }
    return MethodData.model_validate({**base, **kw})


RANKING_METHODS = (
    _method(id="fast", name="Fast", xp_per_hour=[100_000, 100_000], intensity="high",
            cost="expensive"),
    _method(id="afk", name="AFK", xp_per_hour=[60_000, 60_000], intensity="afk", cost="cheap"),
    _method(id="money", name="Money", xp_per_hour=[50_000, 50_000], cost="profit"),
)  # fmt: skip


@pytest.mark.parametrize(
    ("preference", "order"),
    [
        ("fast", ["fast", "afk", "money"]),
        ("afk", ["afk", "money", "fast"]),
        ("profit", ["money", "afk", "fast"]),
        # balanced = speed discounted for clicking: fast 100k x 0.7 = 70k, afk 60k x 1.0,
        # money 50k x 0.85 = 42.5k.
        ("balanced", ["fast", "afk", "money"]),
    ],
)
def test_rankings(
    monkeypatch: pytest.MonkeyPatch, stats_for: Any, preference: Any, order: list[str]
) -> None:
    monkeypatch.setattr(training, "load_methods", lambda: RANKING_METHODS)
    plan = plan_training(
        "agility", stats_for(Agility=xp_for_level(50)), target_level=60, preference=preference
    )
    assert ids(plan.available) == order
    assert plan.route[0].method.id == order[0]


def test_balanced_prefers_afk_when_speeds_are_close(
    monkeypatch: pytest.MonkeyPatch, stats_for: Any
) -> None:
    methods = (
        _method(id="clicky", name="C", xp_per_hour=[100_000, 100_000], intensity="high"),
        _method(id="afk", name="A", xp_per_hour=[80_000, 80_000], intensity="afk"),
    )
    monkeypatch.setattr(training, "load_methods", lambda: methods)
    stats = stats_for(Agility=xp_for_level(50))
    fast = plan_training("agility", stats, target_level=60, preference="fast")
    balanced = plan_training("agility", stats, target_level=60, preference="balanced")
    assert ids(fast.available) == ["clicky", "afk"]
    assert ids(balanced.available) == ["afk", "clicky"]  # 100k x 0.7 = 70k < 80k


def test_balanced_rewards_owned_materials(monkeypatch: pytest.MonkeyPatch, stats_for: Any) -> None:
    methods = (
        _method(id="buy", name="B", xp_each=50, xp_per_hour=[100_000, 100_000], intensity="low"),
        _method(id="own", name="O", xp_each=50, xp_per_hour=[90_000, 90_000], intensity="low",
                recipes=[{"Log": 1}]),
    )  # fmt: skip
    monkeypatch.setattr(training, "load_methods", lambda: methods)
    stats = stats_for(Agility=xp_for_level(50))
    plan = plan_training("agility", stats, target_level=51, owned=owned(Log=100_000))
    assert ids(plan.available) == ["own", "buy"]
    fast = plan_training(
        "agility", stats, target_level=51, owned=owned(Log=100_000), preference="fast"
    )
    assert ids(fast.available) == ["buy", "own"]  # fast ignores ownership unless tied


def test_profit_ties_break_on_speed(monkeypatch: pytest.MonkeyPatch, stats_for: Any) -> None:
    methods = (
        _method(id="slow_profit", name="S", xp_per_hour=[40_000, 40_000], cost="profit"),
        _method(id="quick_profit", name="Q", xp_per_hour=[80_000, 80_000], cost="profit"),
    )
    monkeypatch.setattr(training, "load_methods", lambda: methods)
    plan = plan_training(
        "agility", stats_for(Agility=xp_for_level(50)), target_level=60, preference="profit"
    )
    assert ids(plan.available) == ["quick_profit", "slow_profit"]


# ---------- Edge cases ----------


def test_default_target_is_next_level(stats_for: Any) -> None:
    plan = plan_training("hunter", stats_for(Hunter=xp_for_level(60)))
    assert plan.target_level == 61
    assert set(ids(plan.available)) == {"razor_backed_kebbits", "red_salamanders"}


def test_already_at_target(stats_for: Any) -> None:
    plan = plan_training("hunter", stats_for(Hunter=xp_for_level(65)), target_level=62)
    assert plan.xp_remaining == 0
    assert plan.route == [] and plan.route_gaps == [] and plan.combined_runs is None


def test_uncovered_skill_lists_covered_ones(stats_for: Any) -> None:
    with pytest.raises(InvalidInputError, match="Skills covered so far"):
        plan_training("woodcutting", stats_for())


def test_unranked_skill(stats_for: Any) -> None:
    with pytest.raises(InvalidInputError, match="unranked"):
        plan_training("sailing", stats_for())

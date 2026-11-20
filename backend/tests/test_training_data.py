"""The curated dataset is code: validate every entry so bad data fails CI, not a user."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gielinor_guide.core.skills import SKILLS
from gielinor_guide.core.training import MethodData, load_methods, skills_with_methods


def test_dataset_loads_and_is_well_formed() -> None:
    methods = load_methods()
    assert len(methods) >= 15
    assert len({m.id for m in methods}) == len(methods)
    for m in methods:
        assert m.skill in SKILLS
        assert m.source.startswith("https://")
        if m.kind == "hourly":
            table = m.rate_table()
            assert table[0][0] == m.min_level, m.id
            for _, low, high in table:
                assert 1_000 <= low <= high <= 1_000_000, m.id
        else:
            levels = [t.level for t in m.tiers]
            assert levels == sorted(levels), m.id
            assert all(t.xp_per_patch > 0 for t in m.tiers)


def test_goal_skills_are_covered() -> None:
    # The skills behind the DS2 / WGS goals that motivated the dataset.
    assert {"Smithing", "Thieving", "Herblore", "Farming", "Hunter", "Slayer"} <= set(
        skills_with_methods()
    )


def test_rates_come_from_the_wiki_unless_marked_approximate() -> None:
    for m in load_methods():
        if not m.approximate:
            assert m.source.startswith("https://oldschool.runescape.wiki/"), m.id
        else:
            assert m.variance, m.id


def test_slayer_is_always_approximate() -> None:
    assert all(m.approximate for m in load_methods() if m.skill == "Slayer")


def test_birdhouses_stay_out_after_the_2026_nerf() -> None:
    assert not any("birdhouse" in m.id for m in load_methods())


BASE = {
    "id": "x",
    "skill": "Thieving",
    "name": "X",
    "min_level": 1,
    "intensity": "low",
    "cost": "cheap",
    "source": "https://example.com",
}


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # hourly without xp_per_hour
        {"xp_per_hour": [200, 100]},  # low > high
        {"xp_per_hour": [100, 200], "skill": "Dungeoneering"},
        {"xp_per_hour": [100, 200], "source": "http://insecure.example"},
        {"xp_per_hour": [100, 200], "recipes": [{"Gold ore": 1}]},  # recipes need xp_each
        {"xp_per_hour": [100, 200], "rates": [[1, 150]]},  # two rate fields
        {"rates": [[5, 100]]},  # rate table must start at min_level
        {"rates": [[1, 100], [1, 200]]},  # levels must increase
        {"rates_range": [[1, 300, 200]]},  # low > high
        {"xp_per_hour": [100, 200], "approximate": True},  # approximate needs variance
        {"kind": "runs"},  # runs without tiers
    ],
)
def test_schema_rejects_bad_entries(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MethodData.model_validate({**BASE, **overrides})


def test_skill_aliases_are_normalized() -> None:
    m = MethodData.model_validate({**BASE, "skill": "thiev", "xp_per_hour": [1, 2]})
    assert m.skill == "Thieving"

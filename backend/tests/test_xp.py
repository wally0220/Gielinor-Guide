import pytest

from gielinor_guide.core.errors import InvalidInputError
from gielinor_guide.core.xp import (
    MAX_VIRTUAL_LEVEL,
    Method,
    actions_needed,
    level_for_xp,
    plan_to_level,
    xp_for_level,
)


@pytest.mark.parametrize(
    ("level", "xp"),
    [
        (1, 0),
        (2, 83),
        (10, 1_154),
        (50, 101_333),
        (70, 737_627),
        (73, 992_895),
        (75, 1_210_421),
        (92, 6_517_253),
        (99, 13_034_431),
        (126, 188_884_740),
    ],
)
def test_known_levels(level: int, xp: int) -> None:
    assert xp_for_level(level) == xp


def test_level_for_xp_boundaries() -> None:
    assert level_for_xp(0) == 1
    assert level_for_xp(82) == 1
    assert level_for_xp(83) == 2
    assert level_for_xp(13_034_430) == 98
    assert level_for_xp(13_034_431) == 99
    assert level_for_xp(200_000_000) == 99
    assert level_for_xp(200_000_000, virtual=True) == MAX_VIRTUAL_LEVEL


def test_actions_needed_rounds_up_and_ignores_float_noise() -> None:
    assert actions_needed(78, 78) == 1
    assert actions_needed(79, 78) == 2
    assert actions_needed(0, 78) == 0
    # 56.2 * 10 == 562.0000000000001 in floats; must still be exactly 10.
    assert actions_needed(562, 56.2) == 10


def test_enchant_and_gold_bar_examples() -> None:
    magic = plan_to_level(xp_for_level(73), 75, [Method("Lvl-5 Enchant", 78)])
    assert magic.xp_remaining == 217_526
    assert magic.methods[0].actions == 2_789

    smithing = plan_to_level(xp_for_level(50), 70, [Method("Gold bar (gauntlets)", 56.2)])
    assert smithing.xp_remaining == 636_294
    assert smithing.methods[0].actions == 11_322


def test_plan_when_already_past_target() -> None:
    plan = plan_to_level(xp_for_level(80), 75)
    assert plan.xp_remaining == 0
    assert plan.percent_complete == 100.0


def test_invalid_inputs() -> None:
    with pytest.raises(InvalidInputError):
        xp_for_level(0)
    with pytest.raises(InvalidInputError):
        xp_for_level(127)
    with pytest.raises(InvalidInputError):
        actions_needed(100, 0)

"""Skill names and the common abbreviations players actually type."""

from __future__ import annotations

from osrs_assistant.core.errors import InvalidInputError

# Order matches the in-game skill guide / hiscores (excluding "Overall").
SKILLS: tuple[str, ...] = (
    "Attack", "Hitpoints", "Mining",
    "Strength", "Agility", "Smithing",
    "Defence", "Herblore", "Fishing",
    "Ranged", "Thieving", "Cooking",
    "Prayer", "Crafting", "Firemaking",
    "Magic", "Fletching", "Woodcutting",
    "Runecraft", "Slayer", "Farming",
    "Construction", "Hunter", "Sailing",
)  # fmt: skip

_ALIASES: dict[str, str] = {
    "att": "Attack", "atk": "Attack",
    "str": "Strength",
    "def": "Defence", "defense": "Defence",
    "hp": "Hitpoints", "hitpoint": "Hitpoints",
    "range": "Ranged", "ranging": "Ranged",
    "pray": "Prayer",
    "mage": "Magic",
    "rc": "Runecraft", "runecrafting": "Runecraft",
    "con": "Construction", "cons": "Construction",
    "agil": "Agility", "agi": "Agility",
    "herb": "Herblore",
    "thiev": "Thieving", "thieve": "Thieving",
    "craft": "Crafting",
    "fletch": "Fletching",
    "slay": "Slayer",
    "hunt": "Hunter",
    "mine": "Mining",
    "smith": "Smithing",
    "fish": "Fishing",
    "cook": "Cooking",
    "fm": "Firemaking",
    "wc": "Woodcutting",
    "farm": "Farming",
    "sail": "Sailing",
}  # fmt: skip

_BY_LOWER = {s.lower(): s for s in SKILLS}


def normalize_skill(name: str) -> str:
    """Turn 'mage', 'MAGIC', ' Rc ' into the canonical skill name."""
    key = name.strip().lower()
    if key in _BY_LOWER:
        return _BY_LOWER[key]
    if key in _ALIASES:
        return _ALIASES[key]
    raise InvalidInputError(f"Unknown skill '{name}'. Valid skills: {', '.join(SKILLS)}.")

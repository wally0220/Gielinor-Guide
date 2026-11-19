"""Gielinor Guide MCP server.

Run it with `uv run osrs-mcp` (stdio transport). Claude Code / Claude Desktop start it for you.

Rule for stdio servers: never print() to stdout. Stdout carries the MCP protocol, so stray
output corrupts it. Logging goes to stderr.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from osrs_assistant import __version__
from osrs_assistant.core import xp as xpmath
from osrs_assistant.core.errors import OSRSError
from osrs_assistant.core.hiscores import GameMode, PlayerStats, fetch_player
from osrs_assistant.core.http import OSRSHttp
from osrs_assistant.core.skills import SKILLS, normalize_skill

INSTRUCTIONS = """\
Live Old School RuneScape data. Prefer these tools over memory for anything that changes
(levels, XP, prices) or needs exact numbers.
- If the user says "my stats" / "my levels" and gives no name, call tools without `rsn`;
  the server falls back to the configured default RSN.
- For "how many X to reach level Y", use xp_calc with the user's RSN so the math starts from
  their exact XP, not a rounded level.
"""

READ_ONLY_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)
READ_ONLY_LOCAL = ToolAnnotations(read_only_hint=True, open_world_hint=False)


# ---------- Output schemas (these become the tools' structured output) ----------


class SkillOut(BaseModel):
    skill: str
    level: int | None = Field(description="None if unranked on the hiscores")
    xp: int | None
    rank: int | None = Field(
        description="None if outside the hiscores rank cap (~top 2M); level/xp may still be known"
    )
    xp_to_next_level: int | None


class PlayerStatsOut(BaseModel):
    rsn: str
    mode: str
    total_level: int | None
    total_xp: int | None
    combat_level: int | None
    skills: list[SkillOut]
    unranked_skills: list[str] = Field(
        description="Skills too low to appear on the hiscores; their true level is unknown"
    )


class XpMethodIn(BaseModel):
    name: str = Field(description="e.g. 'Lvl-5 Enchant' or 'Gold bar (goldsmith gauntlets)'")
    xp_each: float = Field(gt=0, description="XP per action/item, e.g. 78 or 56.2")


class XpMethodOut(BaseModel):
    name: str
    xp_each: float
    actions_needed: int


class XpCalcOut(BaseModel):
    skill: str
    source: str = Field(description="Where current XP came from: 'hiscores', 'xp', or 'level'")
    current_level: int
    current_xp: int
    target_level: int
    target_xp: int
    xp_remaining: int
    percent_complete: float
    methods: list[XpMethodOut]


# ---------- Server ----------


def build_server(http: OSRSHttp | None = None) -> MCPServer:
    """Create the server. Tests pass in an OSRSHttp with a mocked transport."""
    client = http or OSRSHttp()
    default_rsn = client.settings.default_rsn

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    server: MCPServer = MCPServer(
        "gielinor-guide",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )

    def resolve_rsn(rsn: str | None) -> str:
        name = (rsn or "").strip() or default_rsn
        if not name:
            raise ToolError("No RSN given and OSRS_DEFAULT_RSN isn't set. Ask the user for it.")
        return name

    async def load_player(rsn: str | None, mode: GameMode) -> PlayerStats:
        try:
            return await fetch_player(client, resolve_rsn(rsn), mode)
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=READ_ONLY_REMOTE)
    async def get_player_stats(
        rsn: str | None = None,
        mode: GameMode = "normal",
        skills: list[str] | None = None,
    ) -> PlayerStatsOut:
        """Get a player's current levels, XP and ranks from the official OSRS hiscores.

        Args:
            rsn: RuneScape name. Omit to use the configured default (the user's own account).
            mode: Hiscores to query: normal, ironman, hardcore, ultimate, deadman, seasonal,
                fresh_start.
            skills: Optional filter, e.g. ["magic", "smithing"]. Abbreviations like "mage",
                "hp", "rc", "con" work. Omit for all skills.

        Data is cached for 3 minutes. Hiscores can lag slightly behind the game.
        """
        player = await load_player(rsn, mode)
        try:
            wanted = [normalize_skill(s) for s in skills] if skills else list(SKILLS)
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

        out: list[SkillOut] = []
        unranked: list[str] = []
        for name in wanted:
            stat = player.skills.get(name)
            if stat is None or not stat.ranked:
                unranked.append(name)
                continue
            assert stat.level is not None and stat.xp is not None
            to_next = (
                xpmath.xp_for_level(stat.level + 1) - stat.xp
                if stat.level < xpmath.MAX_LEVEL
                else 0
            )
            out.append(
                SkillOut(
                    skill=name,
                    level=stat.level,
                    xp=stat.xp,
                    rank=stat.rank,
                    xp_to_next_level=to_next,
                )
            )

        return PlayerStatsOut(
            rsn=player.rsn,
            mode=player.mode,
            total_level=player.overall.level,
            total_xp=player.overall.xp,
            combat_level=player.combat_level,
            skills=out,
            unranked_skills=unranked,
        )

    @server.tool(annotations=READ_ONLY_REMOTE)
    async def xp_calc(
        skill: str,
        target_level: Annotated[int, Field(ge=2, le=xpmath.MAX_VIRTUAL_LEVEL)],
        rsn: str | None = None,
        current_xp: int | None = None,
        current_level: int | None = None,
        methods: list[XpMethodIn] | None = None,
        mode: GameMode = "normal",
    ) -> XpCalcOut:
        """Work out the XP left to reach a target level, and how many actions that takes.

        Starting XP, in priority order: `current_xp` if given, else `current_level` (start of
        that level), else the player's exact XP from the hiscores (`rsn`, or the default RSN).

        Example: skill="magic", target_level=75, methods=[{"name": "Lvl-5 Enchant",
        "xp_each": 78}] -> casts needed. Or skill="smithing", target_level=70,
        methods=[{"name": "Gold bar (goldsmith gauntlets)", "xp_each": 56.2}].

        Pass several methods to compare them side by side.
        """
        try:
            canonical = normalize_skill(skill)
            if current_xp is not None:
                start_xp, source = current_xp, "xp"
            elif current_level is not None:
                start_xp, source = xpmath.xp_for_level(current_level), "level"
            else:
                player = await load_player(rsn, mode)
                stat = player.skills.get(canonical)
                if stat is None or stat.xp is None:
                    raise ToolError(
                        f"{canonical} is unranked on the hiscores for {player.rsn}, so exact XP "
                        "isn't available. Ask for their current level or XP instead."
                    )
                start_xp, source = stat.xp, "hiscores"

            plan = xpmath.plan_to_level(
                start_xp,
                target_level,
                [xpmath.Method(m.name, m.xp_each) for m in methods or []],
            )
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

        return XpCalcOut(
            skill=canonical,
            source=source,
            current_level=plan.current_level,
            current_xp=plan.current_xp,
            target_level=plan.target_level,
            target_xp=plan.target_xp,
            xp_remaining=plan.xp_remaining,
            percent_complete=plan.percent_complete,
            methods=[
                XpMethodOut(name=m.name, xp_each=m.xp_each, actions_needed=m.actions)
                for m in plan.methods
            ],
        )

    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    build_server().run()


if __name__ == "__main__":
    main()

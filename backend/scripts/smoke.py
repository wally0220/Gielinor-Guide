"""Live smoke test: calls the real APIs through the MCP server, without Claude.

uv run python scripts/smoke.py            # uses OSRS_DEFAULT_RSN from backend/.env
uv run python scripts/smoke.py Zezima     # any RSN (account tools need WikiSync/exports)
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp.client.client import Client

from gielinor_guide.mcp.server import build_server


def show(name: str, result: Any, keys: list[str] | None = None) -> None:
    if result.is_error:
        print(f"✗ {name}: {result.content[0].text}")
        return
    data = result.structured_content or {}
    if keys:
        data = {k: data.get(k) for k in keys}
    print(f"✓ {name}:\n{json.dumps(data, indent=2)}")


async def main(rsn: str | None) -> None:
    args = {"rsn": rsn} if rsn else {}
    async with Client(build_server()) as client:
        tools = [t.name for t in (await client.list_tools()).tools]
        print("Tools:", ", ".join(tools))

        show(
            "get_player_stats",
            await client.call_tool("get_player_stats", {**args, "skills": ["magic", "smithing"]}),
        )
        show(
            "xp_calc",
            await client.call_tool(
                "xp_calc",
                {
                    **args,
                    "skill": "magic",
                    "target_level": 75,
                    "methods": [{"name": "Lvl-5 Enchant", "xp_each": 78}],
                },
            ),
        )
        show(
            "get_account_progress",
            await client.call_tool(
                "get_account_progress", {**args, "quests": ["Dragon Slayer II"]}
            ),
            ["rsn", "minutes_since_sync", "quests_completed", "quest_lookup", "notes"],
        )
        show(
            "import_bank",
            await client.call_tool("import_bank", {**args, "search": "cosmic"}),
            ["coins", "equipped", "search_results", "notes"],
        )
        show(
            "suggest_training",
            await client.call_tool("suggest_training", {**args, "skill": "thieving"}),
            ["current_level", "target_level", "route", "route_hours", "notes", "data_sources"],
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))

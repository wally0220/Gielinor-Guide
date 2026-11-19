"""Live smoke test: calls the real APIs through the MCP server, without Claude.

uv run python scripts/smoke.py            # uses OSRS_DEFAULT_RSN from backend/.env
uv run python scripts/smoke.py Zezima     # any RSN
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp.client.client import Client

from gielinor_guide.mcp.server import build_server


async def main(rsn: str | None) -> None:
    args = {"rsn": rsn} if rsn else {}
    async with Client(build_server()) as client:
        tools = [t.name for t in (await client.list_tools()).tools]
        print("Tools:", ", ".join(tools))

        stats = await client.call_tool(
            "get_player_stats", {**args, "skills": ["magic", "smithing"]}
        )
        if stats.is_error:
            print("get_player_stats failed:", stats.content[0].text)  # type: ignore[union-attr]
            return
        print(json.dumps(stats.structured_content, indent=2))

        plan = await client.call_tool(
            "xp_calc",
            {
                **args,
                "skill": "magic",
                "target_level": 75,
                "methods": [{"name": "Lvl-5 Enchant", "xp_each": 78}],
            },
        )
        print(json.dumps(plan.structured_content, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))

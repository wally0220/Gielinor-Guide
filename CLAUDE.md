# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is
Gielinor Guide: live Old School RuneScape data for Claude. A Python core library, exposed as
an MCP server now, and later as a FastAPI backend with a React + TypeScript frontend.

## Layout
- `backend/src/gielinor_guide/core/`: all logic (HTTP, hiscores, XP math, ...). No MCP imports here.
- `backend/src/gielinor_guide/mcp/server.py`: thin MCP wrapper. Tools translate core errors
  (`OSRSError`) into `ToolError`. Groups of tools live in their own module
  (`mcp/account_tools.py`) and are registered from `build_server`.
- Data sources: `core/hiscores.py` (Jagex), `core/wikisync.py` (WikiSync plugin API),
  `core/runelite_export.py` (Data Export plugin files), `core/store.py` (local SQLite history).
- `backend/tests/`: offline tests. HTTP is mocked with `respx`; fixtures live in `tests/fixtures/`.
- `frontend/`: phase 3 (not started).

## Commands (run from `backend/`)
- `uv sync`: install
- `uv run pytest`: tests (must stay offline: never hit real APIs in tests)
- `uv run ruff check . && uv run ruff format .`: lint + format
- `uv run mypy`: strict type check
- `uv run python scripts/smoke.py [RSN]`: live check against the real APIs

## Rules
- Every upstream request goes through `core/http.py` (`OSRSHttp.get_json`), which handles the
  User-Agent, caching, rate limiting and retries. Don't create other HTTP clients.
- Never `print()` in server code: stdout is the MCP stdio channel. Use `logging` (stderr).
- No personal data in committed files. RSN and contact come from `backend/.env` (gitignored).
  Real exports for local debugging go in `backend/.local/` (gitignored); fixtures stay synthetic.
- Tests inject everything with side effects: `build_server(http, store=..., now=...)`.
- MCP SDK is v2: `from mcp.server.mcpserver import MCPServer` (FastMCP was renamed).
- New tools: put logic in `core/`, add a thin tool in `mcp/server.py` with a pydantic output
  model, and add tests at both levels.

## Roadmap
1. ✅ XP math + hiscores (`xp_calc`, `get_player_stats`)
1.1 ✅ Account data: `get_account_progress` (WikiSync), `import_bank` (RuneLite Data Export)
1.2 `suggest_training` (curated `core/data/training_methods.yaml`, uses 1.1 for unlocks/items),
    `get_progress` (hiscores snapshots in the same SQLite store), `next_combat_level`
2. GE prices: `/mapping`, `/latest`, `/1h`, `/timeseries` from prices.runescape.wiki
   (`lookup_item`, `get_prices`, `calc_margin` with 2% GE tax capped at 5m, `get_price_history`)
3. Wiki: `wiki_search`, `wiki_read`, `wiki_infobox` via oldschool.runescape.wiki/api.php
4. `check_quest_requirements`: parse `{{SCP|Skill|Level}}` from quest pages, compare with hiscores
5. FastAPI REST + chat agent loop (BYO key + demo mode), React + TS frontend

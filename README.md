# Gielinor Guide

Live Old School RuneScape data for Claude: your real levels from the hiscores, exact XP math,
and (coming next) Grand Exchange prices, Wiki lookups and quest requirement checks. No more
screenshots, no more guessing.

Built as a Python core library exposed over the
[Model Context Protocol](https://modelcontextprotocol.io), with a web API and React frontend on
the roadmap.

```
                ┌──────────────────────────────┐
                │  osrs_assistant.core         │
                │  hiscores · xp · http/cache  │
                │  (prices · wiki · quests)    │
                └──────┬───────────────┬───────┘
                       │               │
          ┌────────────▼───┐   ┌───────▼──────────────┐
          │  MCP server    │   │  FastAPI (planned)   │
          │  (stdio)       │   │  REST + /chat (SSE)  │
          └───────┬────────┘   └───────┬──────────────┘
                  │                    │
        Claude Code / Desktop    React + TS frontend (planned)
```

## Tools

| Tool | What it does | Source |
|---|---|---|
| `get_player_stats` | Levels, XP, ranks, total and combat level | Official OSRS hiscores |
| `xp_calc` | XP to a target level and actions needed per method | Local formula (+ hiscores for exact XP) |

Planned: `lookup_item`, `get_prices`, `calc_margin`, `get_price_history`, `wiki_search`,
`wiki_read`, `wiki_infobox`, `check_quest_requirements`.

## Quick start (macOS)

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python for you) and
[Claude Code](https://code.claude.com).

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies and run the tests
cd backend
uv sync
uv run pytest

# 3. Configure your RSN (gitignored)
cp .env.example .env    # then edit OSRS_DEFAULT_RSN and OSRS_ASSISTANT_CONTACT

# 4. Live smoke test against the real APIs (no Claude involved)
uv run python scripts/smoke.py
```

### Connect to Claude Code

The repo ships a project-scoped [`.mcp.json`](.mcp.json), so there's nothing to register:

```bash
cd ..            # back to the repo root
claude           # start Claude Code here; approve the "osrs" server when prompted
```

Inside Claude Code, run `/mcp` to confirm `osrs` is connected, then try:

- "What are my current stats?"
- "How many Lvl-5 Enchant casts (78 XP) do I need for 75 Magic?"
- "Compare gold bars with goldsmith gauntlets (56.2 XP) vs. 22.5 XP without, for 70 Smithing."

Prefer registering it yourself? Equivalent command:

```bash
claude mcp add osrs --scope project -- uv --directory "$PWD/backend" run --quiet osrs-mcp
```

### Debug with the MCP Inspector (optional)

```bash
npx @modelcontextprotocol/inspector uv --directory backend run --quiet osrs-mcp
```

## Design notes

- **One HTTP client** (`core/http.py`) for every upstream API: descriptive User-Agent, TTL cache
  (hiscores 3 min), per-host rate limiting (1 req/s), retries with backoff on 429/5xx.
- **Core vs. adapters:** all logic is in `core/` with no MCP imports, so the planned web API
  reuses it unchanged.
- **Structured output:** every tool returns a pydantic model, so clients get a JSON schema and
  typed results instead of free text.
- **Offline tests:** HTTP is mocked with `respx`; MCP tools are tested end-to-end through an
  in-memory MCP client.
- **No personal data in the repo:** RSN and contact info live in `backend/.env`.

## Development

```bash
cd backend
uv run ruff check . && uv run ruff format .
uv run mypy
uv run pytest
```

CI runs all of the above on every push (`.github/workflows/backend.yml`).

## Credits

Hiscores data from Jagex. Price and Wiki data from the
[OSRS Wiki](https://oldschool.runescape.wiki) (CC BY-NC-SA 3.0). Not affiliated with Jagex.

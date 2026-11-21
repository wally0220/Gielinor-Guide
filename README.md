# Gielinor Guide

The Gielinor Guide grew up: an MCP server that gives Claude your live OSRS stats and exact XP
math. Grand Exchange prices, Wiki knowledge and quest requirement checks: Soon™.
No more screenshots, no more guessing.

Built as a Python core library exposed over the
[Model Context Protocol](https://modelcontextprotocol.io), with a web API and React frontend on
the roadmap.

```
                ┌──────────────────────────────┐
                │  gielinor_guide.core         │
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
| `get_account_progress` | Quests, Achievement Diaries, combat achievements, collection log | [WikiSync](https://oldschool.runescape.wiki/w/RuneScape:WikiSync) RuneLite plugin |
| `import_bank` | Bank, inventory and equipment, with search and "what changed since last time" | RuneLite "Data Export" plugin (local files) |
| `suggest_training` | Best route to a goal level with hour ranges, using your unlocks and owned materials | Curated [`training_methods.toml`](backend/src/gielinor_guide/core/data/training_methods.toml) + all of the above |

Planned: `get_progress`, `next_combat_level`, `lookup_item`, `get_prices`, `calc_margin`,
`get_price_history`, `wiki_search`, `wiki_read`, `wiki_infobox`, `check_quest_requirements`.

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
cp .env.example .env    # then edit OSRS_DEFAULT_RSN and GIELINOR_GUIDE_CONTACT

# 4. Live smoke test against the real APIs (no Claude involved)
uv run python scripts/smoke.py
```

### Optional: RuneLite plugins for account data

The hiscores only know levels and XP. Two RuneLite Plugin Hub plugins fill the gaps:

- **WikiSync** (for `get_account_progress`): turn it on and log in; quests, diaries and
  combat achievements sync automatically. For the collection log, open it in game and press
  the WikiSync button (top right).
- **Data Export** (for `import_bank`): enable bank, inventory and equipment in its settings.
  Files are written to `~/.runelite/Data Exports/` when you open your bank or change gear.
  They never leave your machine; snapshots are kept in `~/.local/share/gielinor-guide/`.

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
- "Have I finished the quests Dragon Slayer II needs?"
- "How many cosmic runes do I have, and what changed in my bank since last time?"
- "How should I get 60 Thieving for Dragon Slayer II? I'd rather AFK."
- "What's the fastest way to 70 Smithing with what's in my bank?"
- "Roughly how long to 50 Slayer at Nieve?"

Prefer registering it yourself? Equivalent command:

```bash
claude mcp add osrs --scope project -- uv --directory "$PWD/backend" run --quiet gielinor-guide
```

### Debug with the MCP Inspector (optional)

```bash
npx @modelcontextprotocol/inspector uv --directory backend run --quiet gielinor-guide
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
- **No personal data in the repo:** RSN and contact info live in `backend/.env`; account
  history lives in a local SQLite file outside the repo. Test fixtures are synthetic.
- **Curated data is tested like code:** every training method is schema-validated (pydantic)
  and must link its source. Rates come from the OSRS Wiki's training guides (verified
  2026-11-19), using its per-level tables; Slayer rates are flagged as approximations because
  they depend on your master, tasks and gear.
- **Degrades gracefully:** without WikiSync or a bank export, requirements become "unknown"
  instead of guesses, and the tool says which data source was missing.
- **Built from real payloads:** the WikiSync and Data Export parsers were written against real
  responses, including their quirks (a junk `"."` quest key, JSON Lines files, items whose id
  changes when equipped).

## Development

```bash
cd backend
uv run ruff check . && uv run ruff format .
uv run mypy
uv run pytest
```

CI runs all of the above on every push (`.github/workflows/backend.yml`).

## Contributing training methods

`suggest_training` is only as good as
[`training_methods.toml`](backend/src/gielinor_guide/core/data/training_methods.toml), and adding
a method takes no Python. To add one:

1. **Find the rate on the [OSRS Wiki](https://oldschool.runescape.wiki)** (usually the skill's
   "training" guide) and link that exact section in `source`. Rough community estimates are
   allowed only with `approximate = true` and a `variance` note saying what changes the rate.
2. **Add a `[[method]]` entry.** Use the Wiki's per-level table when there is one:

   ```toml
   [[method]]
   id = "wealthy_citizens"            # unique, snake_case
   skill = "Thieving"
   name = "Stealing valuables / wealthy citizens (Civitas illa Fortis)"
   min_level = 50
   rates = [[50, 72_000], [60, 80_000], [70, 93_000]]   # Wiki table: [level, XP/h]
   casual_factor = 0.9                # low end of the range = rate x this (default 0.8)
   intensity = "afk"                  # afk | low | medium | high
   cost = "profit"                    # profit | cheap | moderate | expensive
   source = "https://oldschool.runescape.wiki/w/Thieving_training#Levels_50–99:_Stealing_valuables_in_Varlamore"
   [method.requires]                  # hard requirements: missing ones lock the method
   quests = ["Children of the Sun"]   # also: quests_started, diaries, items, skills, combat_level
   ```

   Other options: `xp_each` (XP per action) to count actions and owned materials; a flat
   `xp_per_hour = [low, high]` or a `rates_range` table with explicit low/high values;
   alternative ingredient `recipes` (e.g. an unfinished potion *or* herb + vial); `tools` for
   cheap items you just need to bring (reported, never blocking); and `[[method.boosts]]` for
   optional diaries, items or outfits. Farming runs use `kind = "runs"`
   with per-crop `tiers`. The header of the TOML file documents every field.
3. **Run the validator.** Every entry is checked by a pydantic schema in CI:

   ```bash
   cd backend && uv run pytest tests/test_training_data.py
   ```

   Then ask Claude Code for a training plan for that skill to sanity-check the result.

Not yet covered: Agility, Cooking, Construction, Crafting, Firemaking, Fishing, Fletching,
Mining, Prayer, Runecraft, Woodcutting, combat skills and Sailing. Pull requests welcome.

## Credits

Hiscores data from Jagex. Price and Wiki data from the
[OSRS Wiki](https://oldschool.runescape.wiki) (CC BY-NC-SA 3.0). Not affiliated with Jagex.

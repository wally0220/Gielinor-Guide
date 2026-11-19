# Test fixtures

Synthetic data shaped like real API responses and RuneLite exports. None of it is a real
player's account.

- `hiscores_sample.json`: shape of `index_lite.json` (official hiscores)
- `wikisync_sample.json`: shape of `sync.runescape.wiki/runelite/player/<rsn>/STANDARD`,
  including the junk `"."` quest key seen in real responses
- `data_exports/`: JSON Lines files as written by the RuneLite "Data Export" plugin. Graceful
  boots appear with two ids (11860 / 11861), as observed in real exports

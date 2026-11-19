"""Local history in SQLite: item snapshots now, hiscores snapshots next (progress tracking).

The database lives outside the repo (default ~/.local/share/gielinor-guide/history.db), so
personal data never ends up in git.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gielinor_guide.core.runelite_export import ContainerExport, Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_snapshots (
    id          INTEGER PRIMARY KEY,
    rsn         TEXT NOT NULL,
    container   TEXT NOT NULL,
    exported_at TEXT NOT NULL,   -- file mtime, ISO 8601 UTC
    imported_at TEXT NOT NULL,
    source_path TEXT NOT NULL,
    items_json  TEXT NOT NULL,   -- [[id, name, quantity], ...]
    UNIQUE (rsn, container, exported_at)
);
"""


@dataclass(frozen=True)
class ItemChange:
    name: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True)
class SaveResult:
    new: bool
    """False if this exact export (same file timestamp) was already stored."""
    previous_exported_at: datetime | None
    changes: list[ItemChange]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> Store:
        return cls(data_dir / "history.db")

    def close(self) -> None:
        self._db.close()

    def save_items(self, rsn: str, export: ContainerExport) -> SaveResult:
        """Store a snapshot (idempotent) and diff it against the previous one."""
        exported = export.exported_at.astimezone(UTC).isoformat()
        prev = self._db.execute(
            "SELECT exported_at, items_json FROM item_snapshots "
            "WHERE rsn = ? AND container = ? AND exported_at < ? "
            "ORDER BY exported_at DESC LIMIT 1",
            (rsn.lower(), export.container, exported),
        ).fetchone()

        cur = self._db.execute(
            "INSERT OR IGNORE INTO item_snapshots "
            "(rsn, container, exported_at, imported_at, source_path, items_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                rsn.lower(),
                export.container,
                exported,
                datetime.now(UTC).isoformat(),
                str(export.path),
                json.dumps([[i.id, i.name, i.quantity] for i in export.items]),
            ),
        )
        self._db.commit()

        if prev is None:
            return SaveResult(new=cur.rowcount == 1, previous_exported_at=None, changes=[])
        before = _totals_by_name(Item(i, n, q) for i, n, q in json.loads(prev[1]))
        after = _totals_by_name(export.items)
        changes = [
            ItemChange(name, before.get(name, 0), after.get(name, 0))
            for name in sorted(before.keys() | after.keys())
            if before.get(name, 0) != after.get(name, 0)
        ]
        return SaveResult(
            new=cur.rowcount == 1,
            previous_exported_at=datetime.fromisoformat(prev[0]),
            changes=changes,
        )

    def snapshot_count(self, rsn: str, container: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM item_snapshots WHERE rsn = ? AND container = ?",
            (rsn.lower(), container),
        ).fetchone()
        return int(row[0])


def _totals_by_name(items: Iterable[Item]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in items:
        totals[item.name] = totals.get(item.name, 0) + item.quantity
    return totals

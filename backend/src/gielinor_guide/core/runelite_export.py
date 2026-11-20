"""Read item containers exported by the RuneLite "Data Export" plugin.

Observed format (Nov 2026): one file per container, `container_<name>.json`, written as JSON
Lines (one `{"id", "quantity", "name"}` object per line). Files land directly in
`~/.runelite/Data Exports/`, though some setups use a per-account subfolder, so we look in both
and take the newest file for each container. A plain JSON array is accepted too.

Each export overwrites the previous one, so history is kept by `store.py`, not here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from gielinor_guide.core.errors import ExportNotFoundError, UpstreamError

Container = Literal["bank", "inventory", "equipment"]
CONTAINERS: tuple[Container, ...] = ("bank", "inventory", "equipment")

_IGNORED_NAMES = frozenset({"", "null", "bank filler"})


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    quantity: int


@dataclass(frozen=True)
class ContainerExport:
    container: Container
    path: Path
    exported_at: datetime
    items: tuple[Item, ...]

    @property
    def total_quantity(self) -> int:
        return sum(i.quantity for i in self.items)


@dataclass(frozen=True)
class OwnedItems:
    """Bank + inventory + equipment, with name-based lookups.

    Matching is by name because the same item can have several ids: Graceful boots were id
    11860 in an inventory export and 11861 when equipped.
    """

    exports: dict[Container, ContainerExport]

    def _all(self) -> Iterable[tuple[Container, Item]]:
        for container, export in self.exports.items():
            for item in export.items:
                yield container, item

    def count(self, name: str) -> int:
        key = name.strip().lower()
        return sum(i.quantity for _, i in self._all() if i.name.lower() == key)

    def has(self, name: str) -> bool:
        return self.count(name) > 0

    def search(self, text: str) -> list[tuple[Container, Item]]:
        key = text.strip().lower()
        return [(c, i) for c, i in self._all() if key in i.name.lower()]

    def where(self, name: str) -> list[Container]:
        key = name.strip().lower()
        return sorted({c for c, i in self._all() if i.name.lower() == key})


def parse_items(text: str, *, source: str = "export") -> tuple[Item, ...]:
    """Parse JSON Lines (or a JSON array) of {"id", "name", "quantity"} objects."""
    stripped = text.strip()
    if not stripped:
        return ()
    rows: list[Any]
    if stripped.startswith("["):
        try:
            rows = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise UpstreamError(f"{source} is not valid JSON: {exc}") from exc
    else:
        rows = []
        for n, line in enumerate(stripped.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise UpstreamError(f"{source} line {n} is not valid JSON: {exc}") from exc

    items: list[Item] = []
    for row in rows:
        try:
            item = Item(id=int(row["id"]), name=str(row["name"]), quantity=int(row["quantity"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError(f"{source} has an item without id/name/quantity: {row!r}") from exc
        if item.quantity > 0 and item.name.strip().lower() not in _IGNORED_NAMES:
            items.append(item)
    return tuple(items)


def find_export_files(export_dir: Path, rsn: str | None = None) -> dict[Container, Path]:
    """Newest `container_<name>.json` per container, in the folder or one level below it.

    If `rsn` matches a subfolder name, files there win over newer files elsewhere.
    """
    if not export_dir.is_dir():
        raise ExportNotFoundError(
            f"RuneLite export folder not found: {export_dir}. Install the 'Data Export' plugin "
            "from the RuneLite Plugin Hub, open your bank in game, and export."
        )
    found: dict[Container, Path] = {}
    for container in CONTAINERS:
        name = f"container_{container}.json"
        candidates = [p for p in [export_dir / name, *export_dir.glob(f"*/{name}")] if p.is_file()]
        if not candidates:
            continue
        preferred = [p for p in candidates if rsn and p.parent.name.lower() == rsn.lower()]
        found[container] = max(preferred or candidates, key=lambda p: p.stat().st_mtime)
    if not found:
        raise ExportNotFoundError(
            f"No container_*.json files in {export_dir}. In RuneLite, enable the Data Export "
            "plugin's bank/inventory/equipment options, then open your bank and export."
        )
    return found


def read_export(path: Path, container: Container) -> ContainerExport:
    return ContainerExport(
        container=container,
        path=path,
        exported_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        items=parse_items(path.read_text(encoding="utf-8"), source=path.name),
    )


def load_owned_items(
    export_dir: Path,
    *,
    rsn: str | None = None,
    containers: Iterable[Container] = CONTAINERS,
) -> OwnedItems:
    wanted = set(containers)
    files = find_export_files(export_dir, rsn)
    return OwnedItems(
        {c: read_export(p, c) for c, p in files.items() if c in wanted},
    )

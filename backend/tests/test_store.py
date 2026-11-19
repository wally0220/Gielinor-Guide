from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gielinor_guide.core.runelite_export import ContainerExport, Item
from gielinor_guide.core.store import Store


def export(items: list[tuple[str, int]], when: datetime) -> ContainerExport:
    return ContainerExport(
        container="bank",
        path=Path("/tmp/container_bank.json"),
        exported_at=when,
        items=tuple(Item(id=n, name=name, quantity=q) for n, (name, q) in enumerate(items)),
    )


T1 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def test_first_save_then_duplicate(tmp_path: Path) -> None:
    store = Store(tmp_path / "h.db")
    first = store.save_items("Me", export([("Coins", 100)], T1))
    again = store.save_items("me", export([("Coins", 100)], T1))  # rsn is case-insensitive
    assert first.new and first.changes == [] and first.previous_exported_at is None
    assert not again.new
    assert store.snapshot_count("ME", "bank") == 1
    store.close()


def test_diff_against_previous_snapshot(tmp_path: Path) -> None:
    store = Store(tmp_path / "h.db")
    store.save_items("Me", export([("Coins", 100), ("Gold ore", 50), ("Feather", 5)], T1))
    result = store.save_items("Me", export([("Coins", 250), ("Gold bar", 50), ("Feather", 5)], T2))
    assert result.new
    assert result.previous_exported_at == T1
    assert {(c.name, c.before, c.after, c.delta) for c in result.changes} == {
        ("Coins", 100, 250, 150),
        ("Gold bar", 0, 50, 50),
        ("Gold ore", 50, 0, -50),
    }
    store.close()


def test_accounts_are_separate(tmp_path: Path) -> None:
    store = Store(tmp_path / "h.db")
    store.save_items("Main", export([("Coins", 1)], T1))
    other = store.save_items("Alt", export([("Coins", 9)], T2))
    assert other.previous_exported_at is None
    store.close()


def test_persists_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "h.db"
    Store(path).save_items("Me", export([("Coins", 1)], T1))
    assert Store(path).snapshot_count("me", "bank") == 1

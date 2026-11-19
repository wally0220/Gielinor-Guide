from __future__ import annotations

import os
from pathlib import Path

import pytest

from gielinor_guide.core.errors import ExportNotFoundError, UpstreamError
from gielinor_guide.core.runelite_export import find_export_files, load_owned_items, parse_items


def test_parse_jsonl_filters_junk() -> None:
    text = (
        '{"id": 995, "quantity": 10, "name": "Coins"}\n'
        "\n"
        '{"id": 20594, "quantity": 1, "name": "Bank filler"}\n'
        '{"id": 1, "quantity": 0, "name": "Placeholder"}\n'
        '{"id": 564, "quantity": 3, "name": "Cosmic rune"}\n'
    )
    items = parse_items(text)
    assert [(i.name, i.quantity) for i in items] == [("Coins", 10), ("Cosmic rune", 3)]


def test_parse_json_array_and_empty() -> None:
    assert parse_items('[{"id": 1, "quantity": 2, "name": "Feather"}]')[0].quantity == 2
    assert parse_items("   ") == ()


def test_parse_reports_bad_line_number() -> None:
    with pytest.raises(UpstreamError, match="line 2"):
        parse_items('{"id": 1, "quantity": 1, "name": "a"}\n{oops', source="bank")


def test_parse_rejects_missing_fields() -> None:
    with pytest.raises(UpstreamError, match="id/name/quantity"):
        parse_items('{"id": 1, "name": "no quantity"}')


def test_owned_items_match_by_name_across_ids(export_dir: Path) -> None:
    owned = load_owned_items(export_dir)
    assert set(owned.exports) == {"bank", "inventory", "equipment"}
    # Graceful boots: id 11860 in inventory, 11861 equipped.
    assert owned.count("graceful boots") == 2
    assert owned.where("Graceful boots") == ["equipment", "inventory"]
    assert owned.has("Goldsmith gauntlets")
    assert not owned.has("Twisted bow")
    assert [i.name for _, i in owned.search("graceful")] == [
        "Graceful gloves",
        "Graceful boots",
        "Graceful boots",
    ]


def test_container_filter(export_dir: Path) -> None:
    owned = load_owned_items(export_dir, containers=["equipment"])
    assert list(owned.exports) == ["equipment"]


def test_finds_newest_file_in_root_or_subfolder(export_dir: Path) -> None:
    sub = export_dir / "OtherAccount"
    sub.mkdir()
    newer = sub / "container_bank.json"
    newer.write_text('{"id": 995, "quantity": 1, "name": "Coins"}\n')
    root = export_dir / "container_bank.json"
    os.utime(root, (1_000_000, 1_000_000))
    assert find_export_files(export_dir)["bank"] == newer


def test_rsn_subfolder_wins_even_if_older(export_dir: Path) -> None:
    mine = export_dir / "SamplePlayer"
    mine.mkdir()
    bank = mine / "container_bank.json"
    bank.write_text('{"id": 995, "quantity": 1, "name": "Coins"}\n')
    os.utime(bank, (1_000_000, 1_000_000))
    assert find_export_files(export_dir, rsn="sampleplayer")["bank"] == bank


def test_missing_folder_and_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(ExportNotFoundError, match="Data Export"):
        find_export_files(tmp_path / "nope")
    with pytest.raises(ExportNotFoundError, match="No container_"):
        find_export_files(tmp_path)

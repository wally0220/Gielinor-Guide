from pathlib import Path

import pytest

from osrs_assistant.core.config import Settings, load_env_file


def test_env_file_loads_without_overriding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nOSRS_DEFAULT_RSN="Zezima"\nexport OSRS_ASSISTANT_CONTACT=me\n')
    # setenv then delenv so monkeypatch restores the original state afterwards.
    monkeypatch.setenv("OSRS_DEFAULT_RSN", "placeholder")
    monkeypatch.delenv("OSRS_DEFAULT_RSN")
    monkeypatch.setenv("OSRS_ASSISTANT_CONTACT", "already-set")

    load_env_file(env)

    import os

    assert os.environ["OSRS_DEFAULT_RSN"] == "Zezima"
    assert os.environ["OSRS_ASSISTANT_CONTACT"] == "already-set"


def test_user_agent_includes_contact_only_when_set() -> None:
    assert "@me" in Settings(contact="@me on Discord", default_rsn=None).user_agent
    assert " - " not in Settings(contact=None, default_rsn=None).user_agent

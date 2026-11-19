"""Error types. Messages are written to be shown directly to a user (or to Claude)."""

from __future__ import annotations


class OSRSError(Exception):
    """Base class for all expected, user-facing errors."""


class PlayerNotFoundError(OSRSError):
    def __init__(self, rsn: str, mode: str) -> None:
        super().__init__(
            f"No {mode} hiscores entry for '{rsn}'. Check the spelling, the game mode, "
            "and that the account is ranked in at least one skill."
        )
        self.rsn = rsn
        self.mode = mode


class UpstreamError(OSRSError):
    """An upstream API failed or returned something unexpected."""


class InvalidInputError(OSRSError):
    """The caller passed something that can't be used (unknown skill, bad level, ...)."""

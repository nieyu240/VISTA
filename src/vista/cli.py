"""Single API entry point for the team's game benchmark."""

from __future__ import annotations

from collections.abc import Sequence

from .localbench import main as local_main


def main(argv: Sequence[str] | None = None) -> int:
    return local_main(argv)

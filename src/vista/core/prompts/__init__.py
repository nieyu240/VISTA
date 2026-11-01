"""Shared VISTA method prompts; benchmarks own their task objectives."""

from importlib.resources import files


def vista_method(backend: str) -> str:
    if backend != "api":
        raise ValueError("This benchmark uses the API backend")
    return files(__package__).joinpath("game.md").read_text(encoding="utf-8")

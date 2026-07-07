"""Frozen backtest engine (R1). Built during setup, never edited by the
overnight loop, mounted read-only in Docker. Contracts live in SPEC.md."""

from engine.errors import EngineError

__all__ = ["EngineError"]

"""Path resolution based on RP_HOME (defaults to the project root)."""

from __future__ import annotations

import os
from pathlib import Path

# src/reviewpulse/paths.py -> project root is two levels above the package dir
_DEFAULT_HOME = Path(__file__).resolve().parents[2]


def rp_home() -> Path:
    """Return the base directory for all relative paths."""
    env = os.environ.get("RP_HOME")
    return Path(env).resolve() if env else _DEFAULT_HOME


def resolve(path: str | Path) -> Path:
    """Resolve a path relative to RP_HOME unless it is already absolute."""
    p = Path(path)
    return p if p.is_absolute() else rp_home() / p

"""Timestamp helpers.

`infer_timestamp_unit` is only a spike/diagnostic aid. The prepare step never
guesses: it requires `timestamp_unit` to be declared in source.yaml.
"""

from __future__ import annotations

from collections.abc import Iterable

# Epoch seconds for 2001-09-09 .. 2286-11-20 have 10 digits; epoch ms have 13.
_DIGITS_TO_UNIT = {10: "s", 13: "ms"}


def infer_timestamp_unit(values: Iterable[int]) -> str:
    """Return "s" or "ms" if every value has the same, unambiguous digit count."""
    digit_counts = set()
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"timestamp must be a positive int, got {v!r}")
        digit_counts.add(len(str(v)))
    if not digit_counts:
        raise ValueError("no timestamps given")
    if len(digit_counts) != 1:
        raise ValueError(f"mixed timestamp digit counts: {sorted(digit_counts)}")
    (digits,) = digit_counts
    if digits not in _DIGITS_TO_UNIT:
        raise ValueError(f"ambiguous timestamp digit count: {digits}")
    return _DIGITS_TO_UNIT[digits]

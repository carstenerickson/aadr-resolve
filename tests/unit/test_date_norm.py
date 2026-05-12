"""Unit tests for date_norm.py."""

from __future__ import annotations

import pandas as pd
import pytest

from aadr_resolve.date_norm import to_int64_nullable, to_int64_nullable_defensive


def test_strict_path_clean_integers() -> None:
    s = pd.Series(["0", "70", "8000", "185000"])
    out = to_int64_nullable(s)
    assert str(out.dtype) == "Int64"
    assert out.isna().sum() == 0
    assert out.tolist() == [0, 70, 8000, 185000]


def test_strict_path_empty_becomes_na() -> None:
    """The empty-string-to-NA guard pin (HLD §`.anno` loader)."""
    s = pd.Series(["1000", "", "2000", ""])
    out = to_int64_nullable(s)
    assert str(out.dtype) == "Int64"
    assert out.isna().sum() == 2
    assert out.dropna().tolist() == [1000, 2000]


def test_strict_path_negative_value() -> None:
    """v66 has 4 samples with date_calbp = -4 (apparent upstream typo);
    pin: accepted as-is, not coerced."""
    s = pd.Series(["-4", "1000"])
    out = to_int64_nullable(s)
    assert out.tolist() == [-4, 1000]


def test_strict_path_non_numeric_raises() -> None:
    """The strict path rejects non-integer strings — by design. Use the
    defensive variant when the input may have garbage."""
    s = pd.Series(["1000", "not_a_number"])
    with pytest.raises((ValueError, TypeError)):
        to_int64_nullable(s)


def test_defensive_path_coerces_garbage_to_na() -> None:
    s = pd.Series(["1000", "not_a_number", "", "2000"])
    out = to_int64_nullable_defensive(s)
    assert str(out.dtype) == "Int64"
    assert out.isna().sum() == 2
    assert out.dropna().tolist() == [1000, 2000]

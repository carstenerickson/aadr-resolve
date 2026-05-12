"""Int64-nullable date normalization. Per LLD §3.4."""

from __future__ import annotations

import pandas as pd


def to_int64_nullable(raw_series: pd.Series) -> pd.Series:
    """Convert a string Series (from the loader) to nullable Int64.

    Pipeline:
      s = raw_series.replace('', pd.NA)   # empty-cell guard (HLD-pinned)
      return s.astype('Int64')

    Used for date_calbp, date_sd_bp, persistent_genetic_id. Bench-verify
    showed 0 nulls in date columns across all 6 public-release `.anno` files;
    the empty-to-NA guard is defensive against future releases and non-public
    builds where nulls may appear.

    Raises ValueError if any non-empty cell can't be parsed as an integer.
    Use to_int64_nullable_defensive() to tolerate bad values."""
    s = raw_series.replace("", pd.NA)
    return s.astype("Int64")


def to_int64_nullable_defensive(raw_series: pd.Series) -> pd.Series:
    """Tolerates non-integer strings by coercing to NA.

    Pipeline:
      s = pd.to_numeric(raw_series, errors='coerce')   # invalid -> NaN
      return s.astype('Int64')                          # NaN -> <NA>

    Used by tests against synthetic .anno files that intentionally include
    bad rows. NOT used by the production loader path (which trusts the
    bench-verified clean-integer property)."""
    return pd.to_numeric(raw_series, errors="coerce").astype("Int64")

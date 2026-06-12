"""Int64-nullable date normalization. Per LLD §3.4."""

from __future__ import annotations

import numpy as np
import pandas as pd


def to_int64_nullable(raw_series: pd.Series) -> pd.Series:
    """STRICT string -> nullable Int64: empty cells become <NA>; any other
    non-integer cell RAISES.

    Pipeline:
      s = raw_series.replace('', pd.NA)   # empty-cell guard (HLD-pinned)
      return s.astype('Int64')

    A strict validator that asserts the bench-verified clean-integer property
    (0 nulls in date columns across all 6 public releases). The production
    accessors use to_int64_nullable_defensive() instead, which tolerates dirty
    cells; this strict form is retained for tests and callers that want a hard
    failure on malformed input."""
    s = raw_series.replace("", pd.NA)
    return s.astype("Int64")


def to_int64_nullable_defensive(raw_series: pd.Series) -> pd.Series:
    """Tolerant string -> nullable Int64: any cell that isn't a finite whole
    number becomes <NA> (never raises).

    Pipeline:
      num = pd.to_numeric(raw_series, errors='coerce')   # non-numeric -> NaN
      keep only finite, integral values                  # '12.5'/'inf' -> <NA>
      return num.astype('Int64')

    This is the production path for every Int64 accessor (date_calbp, date_sd_bp,
    persistent_genetic_id, snps_hit_1240k, lookup): a dirty future/non-public
    release degrades to <NA> instead of crashing the load. For the bench-verified
    clean public releases this is a no-op (every cell is already a whole integer).
    Note pd.to_numeric alone is NOT enough — its float result raises on a
    fractional/inf value at .astype('Int64'), so non-integral values are masked
    to <NA> first."""
    num = pd.to_numeric(raw_series, errors="coerce")
    integral = np.isfinite(num) & (np.floor(num) == num)
    return num.where(integral).astype("Int64")

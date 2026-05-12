"""Float64 coverage normalization. Per LLD §3.5 and HLD §Coverage normalization."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pandas as pd

from .errors import MissingNativeFieldError
from .types import SchemaClass

if TYPE_CHECKING:
    from .annoframe import AnnoFrame

# Pinned constant: 1240k SNP panel cardinality (HLD §Coverage normalization).
# Module-level so test fixtures (which use much smaller mini-panels) can
# monkeypatch it for synthetic-data scenarios.
PANEL_CARDINALITY_1240K: int = 1148000


def resolve_coverage(af: AnnoFrame, canonical_field: str) -> pd.Series:
    """Return a Float64 Series for the requested canonical coverage field.

    Routing logic (HLD §Coverage normalization):

      - canonical_field == 'coverage_1240k':
          - Classes A, B, C, E: read native column, coerce to Float64.
            Class A maps to col 20 'Coverage on autosomal targets' (bam-cov
            proxy; documented as imperfect for 1240k semantics).
          - Class D (v62): NO native coverage column. Returns an all-NaN
            Float64 Series of length af.n_rows. No warning emitted here —
            aadr-subset's filter layer surfaces the user-facing warning when
            its min_coverage filter selects nothing.

      - canonical_field == 'snps_hit_1240k' (the derived-proxy path):
          - All classes (A/B/C/D/E have this field per schema YAMLs).
          - Returns Float64 series of count / PANEL_CARDINALITY_1240K, giving
            a fraction-of-1240k-sites-observed proxy.
          - Emits ONE stderr warning per (AnnoFrame instance, canonical_field)
            pair. Cached via the AnnoFrame's _coverage_cache mechanism so
            repeated calls don't spam stderr.

      - Any other canonical_field in the schema:
          - Looks up the column; pd.to_numeric(errors='coerce') for Float64.
          - No warning; the caller knows what they asked for.

      - Field absent in schema:
          - For 'coverage_1240k' on class D → all-NaN (the documented default).
          - For any other absent field → raises MissingNativeFieldError."""
    schema_class = af.schema_class

    # Special case: class D has no native 1240k coverage column.
    # Return all-NaN Float64 Series rather than raising; documented HLD behavior.
    if canonical_field == "coverage_1240k" and not af.schema_def.has_field("coverage_1240k"):
        if schema_class == SchemaClass.D:
            return pd.Series([pd.NA] * af.n_rows, dtype="Float64")
        # Other classes lacking coverage_1240k are an invariant violation.
        raise MissingNativeFieldError(
            f"coverage_1240k not present in schema class {schema_class.value} "
            f"(only class D's all-NaN return is documented)"
        )

    # snps_hit_1240k -> derived proxy with stderr warning.
    if canonical_field == "snps_hit_1240k":
        raw = af._raw_column("snps_hit_1240k")
        counts = pd.to_numeric(raw, errors="coerce")
        proxy = counts / PANEL_CARDINALITY_1240K
        proxy = proxy.astype("Float64")
        # Emit the Poisson-divergence warning once per AnnoFrame instance.
        if "snps_hit_1240k" not in af._coverage_cache:
            sys.stderr.write(_proxy_warning_text(file_label=af.version) + "\n")
        return proxy

    # General path: any other canonical field present in the schema.
    if not af.schema_def.has_field(canonical_field):
        raise MissingNativeFieldError(
            f"field {canonical_field!r} not present in schema class {schema_class.value}"
        )
    raw = af._raw_column(canonical_field)
    return _coerce_float64(raw)


def _coerce_float64(raw_series: pd.Series) -> pd.Series:
    """Convert a string Series to Float64; NaN for empty / non-numeric.

    v52's `\\xef\\xbf\\xbd` Unicode-replacement-character prefix on numeric
    cells gets coerced to NaN (loss <0.2% of v52 rows; HLD-documented).
    """
    return pd.to_numeric(raw_series, errors="coerce").astype("Float64")


def _proxy_warning_text(*, file_label: str) -> str:
    """The stderr warning text for the class-D snps_hit-derived proxy.

    Restates the Poisson-divergence math from HLD §Coverage normalization
    so the user sees the caveat at runtime (not just in the docs)."""
    return (
        f"WARNING: {file_label} has no native coverage column. Using "
        f"'snps_hit_1240k' as derived proxy: coverage_proxy = "
        f"snps_hit_1240k / {PANEL_CARDINALITY_1240K}. This is "
        f"fraction-of-1240k-sites-observed, NOT mean coverage. At low depth "
        f"they diverge: 1.0x true coverage -> 0.63 proxy; 0.5x true -> 0.39 "
        f"proxy. Adjust your threshold accordingly: min_coverage: 0.5 "
        f"against the proxy excludes samples with true coverage up to ~1.2x."
    )

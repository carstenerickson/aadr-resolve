"""AnnoFrame: the central library data type. Per LLD §2.4 + §3.6.

Day-1 scaffold: typed accessors for string columns (genetic_id, individual_id,
group_id, persistent_genetic_id). The Float64/Int64 accessors (date_calbp,
date_sd_bp, coverage, coverage_via) raise NotImplementedError — they land in
Day 2 alongside date_norm.py and coverage_norm.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import pandas as pd

from .types import SchemaClass, SchemaClassDef


@dataclass
class AnnoFrame:
    """Schema-resolved .anno reader. Library-level public API.

    See HLD §Library API surface and LLD §2.4 for the contract."""

    version: str  # parsed version label, e.g., "v66.0"
    schema_class: SchemaClass  # detected or override
    schema_def: SchemaClassDef  # loaded YAML for the class
    df: pd.DataFrame  # raw string-dtype cells, indexed 0..n_rows-1
    # Original .anno path the loader read from. None when constructed
    # directly (tests, in-memory cases). Day-6 addition so sibling tools
    # (e.g., aadr-subset) can call resolve_master_ids without re-tracking
    # paths separately.
    path: Path | None = field(default=None, compare=False)
    # Day-2 caches; declared here so the dataclass shape is stable.
    _date_calbp_cache: pd.Series | None = field(default=None, repr=False, compare=False)
    _coverage_cache: dict[str, pd.Series] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_path(
        cls,
        path: Path | str,
        *,
        version_label: str | None = None,
        schema_override: SchemaClass | None = None,
    ) -> Self:
        """Load an .anno end-to-end. Delegates to loader.read_anno()."""
        # Local import to break the loader <-> annoframe cycle.
        from .loader import read_anno

        return read_anno(  # type: ignore[return-value]
            Path(path),
            version_label=version_label,
            schema_override=schema_override,
        )

    # === Cardinality ===

    @property
    def n_rows(self) -> int:
        return len(self.df)

    @property
    def n_columns(self) -> int:
        return int(self.df.shape[1])

    # === Identity columns (always string dtype) ===

    @property
    def genetic_id(self) -> pd.Series:
        return self._raw_column("genetic_id").astype("string").copy()

    @property
    def individual_id(self) -> pd.Series:
        return self._raw_column("individual_id").astype("string").copy()

    @property
    def group_id(self) -> pd.Series:
        return self._raw_column("group_id").astype("string").copy()

    @property
    def persistent_genetic_id(self) -> pd.Series | None:
        """v66+ only (class E). Returns None for every class but E.

        Int64 nullable Series of the numeric Persistent Genetic ID column."""
        if self.schema_class != SchemaClass.E:
            return None
        from .date_norm import to_int64_nullable

        raw = self._raw_column("persistent_genetic_id")
        return to_int64_nullable(raw).copy()

    # === Int64-nullable date accessors ===

    @property
    def date_calbp(self) -> pd.Series:
        """Canonical calBP (integer years before 1950 CE) as nullable Int64.

        Cached on first access; cleared by reset_caches(). Per HLD §Date
        normalization, bench-verified clean across all 6 versions (0 nulls;
        range 0-185000)."""
        if self._date_calbp_cache is None:
            from .date_norm import to_int64_nullable

            raw = self._raw_column("date_mean_bp")
            self._date_calbp_cache = to_int64_nullable(raw)
        return self._date_calbp_cache.copy()

    @property
    def date_sd_bp(self) -> pd.Series:
        """Date SD (BP) as nullable Int64. Used by CI-aware time-series cohorts."""
        from .date_norm import to_int64_nullable

        raw = self._raw_column("date_sd_bp")
        return to_int64_nullable(raw).copy()

    # === Float64 coverage accessors ===

    @property
    def coverage(self) -> pd.Series:
        """1240k-target coverage Float64. NaN for missing.

        For class D (v62, no native column) returns an all-NaN Series of
        length n_rows. Use coverage_via('snps_hit_1240k') for the derived
        proxy (with Poisson-divergence stderr warning)."""
        return self.coverage_via("coverage_1240k")

    def coverage_via(self, canonical_field: str) -> pd.Series:
        """Float64 coverage from a specified canonical field.

        See coverage_norm.resolve_coverage for the per-class routing logic
        + the Poisson-divergence caveat when 'snps_hit_1240k' is requested.

        Results are cached on the AnnoFrame instance (one cache per
        canonical_field); copies are returned to library consumers so
        mutation by the caller doesn't corrupt the cache."""
        if canonical_field in self._coverage_cache:
            return self._coverage_cache[canonical_field].copy()

        from .coverage_norm import resolve_coverage

        series = resolve_coverage(self, canonical_field)
        self._coverage_cache[canonical_field] = series
        return series.copy()

    def reset_caches(self) -> None:
        """Clear the typed-accessor caches.

        Useful in tests when the same AnnoFrame instance is reused across
        scenarios that monkeypatch coverage_norm.PANEL_CARDINALITY_1240K
        (the cached values would otherwise reflect the pre-patch state)."""
        self._date_calbp_cache = None
        self._coverage_cache.clear()

    # === Internal helpers ===

    def _raw_column(self, canonical_field: str) -> pd.Series:
        """Raw string Series for a canonical field. Raises if absent in class.

        Passes self.version so per-version column overrides (releases that share a
        detection signature but relocate fields) resolve to the right column."""
        col_idx = self.schema_def.column_for(canonical_field, version=self.version)
        return self.df.iloc[:, col_idx - 1]

    # === Diagnostic ===

    def to_dict(self) -> dict[str, Any]:
        """Serializable summary for the `schema` subcommand's JSON output."""
        mapped_fields: dict[str, dict[str, Any]] = {}
        # Report the column actually used for THIS release, not the base layout —
        # version_overrides can relocate a field (e.g. v50.0 dates).
        resolved = self.schema_def.resolved_columns(self.version)
        for canonical, mapping in self.schema_def.fields.items():
            col, base = resolved[canonical]
            entry: dict[str, Any] = {
                "column": col,
                "normalized_header": mapping.normalized_header,
                "display_header": mapping.display_header,
            }
            if base is not None:
                entry["base_column"] = base
            mapped_fields[canonical] = entry
        return {
            "version": self.version,
            "schema_class": self.schema_class.value,
            "n_rows": self.n_rows,
            "n_columns": self.n_columns,
            "detection_signature": list(self.schema_def.detection_signature),
            "fields": mapped_fields,
            "not_present": list(self.schema_def.not_present),
            "notes": list(self.schema_def.notes),
        }

    def __repr__(self) -> str:
        return (
            f"AnnoFrame(version={self.version!r}, "
            f"schema_class={self.schema_class.value!r}, "
            f"n_rows={self.n_rows}, n_columns={self.n_columns})"
        )


def ensure_unique_versions(anno_frames: list[AnnoFrame]) -> None:
    """Reject two frames that share a version label but belong to DIFFERENT schema
    classes.

    The N-frame cross-version flows (cohort manifest, lookup) key per-version state
    by `version_label`. Before class F, each version label mapped to exactly one
    class, so a label uniquely identified a layout. Class F (early Human Origins)
    newly shares v44.3/v50.0 with class A (1240K), so the v50.0 1240K and v50.0 HO
    panels both infer `v50.0` while carrying *different* data — keying both by
    `v50.0` would silently overwrite one panel (last-writer-wins). Reject that.

    (Two same-version, same-class frames remain allowed: that is a pre-existing
    degenerate case the join/turnover flows rely on, and `diff`/`join` compare two
    frames positionally rather than by a version-keyed dict.)"""
    from .errors import UsageError

    seen: dict[str, AnnoFrame] = {}
    for af in anno_frames:
        prev = seen.get(af.version)
        if prev is not None and prev.schema_class != af.schema_class:
            raise UsageError(
                f"two .anno files share version label {af.version!r} but are different "
                f"schema classes ({prev.schema_class.value} and {af.schema_class.value}) — "
                f"e.g. the 1240K and Human Origins panels of one release. These flows key "
                f"per-version state by label, so they can't be combined in a single run. "
                f"Supply one .anno per version, or pass distinct --version-label values."
            )
        seen.setdefault(af.version, af)

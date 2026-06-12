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
    # The version_overrides layout key selected from the actual header content
    # (None = base layout). Set by the loader; drives column resolution so that a
    # release with a shifted layout is read correctly even when its filename
    # version label is wrong or couldn't be inferred. See SchemaClassDef.
    layout_version: str | None = field(default=None, compare=False)
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
        from .date_norm import to_int64_nullable_defensive

        raw = self._raw_column("persistent_genetic_id")
        return to_int64_nullable_defensive(raw).copy()

    # === Int64-nullable date accessors ===

    @property
    def date_calbp(self) -> pd.Series:
        """Canonical calBP (integer years before 1950 CE) as nullable Int64.

        Cached on first access; cleared by reset_caches(). Per HLD §Date
        normalization, bench-verified clean across all 6 versions (0 nulls;
        range 0-185000). Non-integer cells in a future/non-public release coerce
        to <NA> rather than raising (consistent with coverage's float coercion)."""
        if self._date_calbp_cache is None:
            from .date_norm import to_int64_nullable_defensive

            raw = self._raw_column("date_mean_bp")
            self._date_calbp_cache = to_int64_nullable_defensive(raw)
        return self._date_calbp_cache.copy()

    @property
    def date_sd_bp(self) -> pd.Series:
        """Date SD (BP) as nullable Int64. Used by CI-aware time-series cohorts.

        Non-integer cells coerce to <NA> rather than raising (a dirty future
        release degrades gracefully instead of crashing the load)."""
        from .date_norm import to_int64_nullable_defensive

        raw = self._raw_column("date_sd_bp")
        return to_int64_nullable_defensive(raw).copy()

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

        Resolves via self.layout_version — the layout the loader selected from the
        actual headers — so a release with a shifted column layout reads correctly
        regardless of its filename version label."""
        col_idx = self.schema_def.column_for(canonical_field, version=self.layout_version)
        return self.df.iloc[:, col_idx - 1]

    # === Diagnostic ===

    def to_dict(self) -> dict[str, Any]:
        """Serializable summary for the `schema` subcommand's JSON output."""
        mapped_fields: dict[str, dict[str, Any]] = {}
        # Report the column actually used for THIS release, not the base layout —
        # version_overrides can relocate a field (e.g. v50.0 dates).
        resolved = self.schema_def.resolved_columns(self.layout_version)
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
    """Reject any two frames that share a version label.

    The cross-version flows (cohort, join, lookup) store per-version state by
    `version_label` in plain dicts, so two frames at one label silently overwrite
    each other — one frame's data vanishes with no error. A shared label arises two
    ways, both rejected because the per-version data collides either way:

      - DIFFERENT classes: class F (early Human Origins) shares v44.3/v50.0 with
        class A (1240K), so the 1240K and HO panels of one release both infer e.g.
        `v50.0` while carrying different data.
      - SAME class: a patch release infers the same label as its base (e.g.
        `aadr_v54.1.p1_..._public.anno` and `aadr_v54.1_..._public.anno` both infer
        `v54.1`, both class C), or the same file is supplied twice.

    Every cross-version flow spans distinct versions, so a shared label is always a
    mistake — there is no flow that legitimately pairs two frames at one label."""
    from .errors import UsageError

    seen: dict[str, AnnoFrame] = {}
    for af in anno_frames:
        prev = seen.get(af.version)
        if prev is not None:
            same_class = prev.schema_class == af.schema_class
            detail = (
                f"both schema class {af.schema_class.value} — e.g. a release and its "
                ".p1 patch, or the same file twice"
                if same_class
                else f"different schema classes ({prev.schema_class.value} and "
                f"{af.schema_class.value}) — e.g. the 1240K and Human Origins panels "
                "of one release"
            )
            raise UsageError(
                f"two .anno files share version label {af.version!r} ({detail}). These "
                f"flows key per-version state by label, so they can't be combined in a "
                f"single run. Supply one .anno per version, or pass distinct "
                f"--version-label values."
            )
        seen[af.version] = af

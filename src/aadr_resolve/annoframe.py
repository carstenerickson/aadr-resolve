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
        """v66+ only (class E). Returns None for classes A-D.

        Day-1 scaffold returns the raw string Series for class E (cast to Int64
        lands in Day 2 alongside date_norm.to_int64_nullable)."""
        if self.schema_class != SchemaClass.E:
            return None
        # Day 2 will replace with to_int64_nullable.
        return self._raw_column("persistent_genetic_id").astype("string").copy()

    # === Day-2 stubs ===

    @property
    def date_calbp(self) -> pd.Series:
        raise NotImplementedError(
            "AnnoFrame.date_calbp lands in Day 2 (date_norm.to_int64_nullable). "
            "Day 1 only exposes string identity columns."
        )

    @property
    def date_sd_bp(self) -> pd.Series:
        raise NotImplementedError("AnnoFrame.date_sd_bp lands in Day 2.")

    @property
    def coverage(self) -> pd.Series:
        raise NotImplementedError(
            "AnnoFrame.coverage lands in Day 2 (coverage_norm.resolve_coverage)."
        )

    def coverage_via(self, canonical_field: str) -> pd.Series:
        raise NotImplementedError("AnnoFrame.coverage_via lands in Day 2.")

    # === Internal helpers ===

    def _raw_column(self, canonical_field: str) -> pd.Series:
        """Raw string Series for a canonical field. Raises if absent in class."""
        col_idx = self.schema_def.column_for(canonical_field)
        return self.df.iloc[:, col_idx - 1]

    # === Diagnostic ===

    def to_dict(self) -> dict[str, Any]:
        """Serializable summary for the `schema` subcommand's JSON output."""
        mapped_fields: dict[str, dict[str, Any]] = {}
        for canonical, mapping in self.schema_def.fields.items():
            mapped_fields[canonical] = {
                "column": mapping.column,
                "normalized_header": mapping.normalized_header,
                "display_header": mapping.display_header,
            }
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

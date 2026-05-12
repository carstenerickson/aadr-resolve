"""Shared dataclasses and enums.

Per LLD §2. Day-1 scope: SchemaClass, ExitCode, FieldMapping, SchemaClassDef.
Day-3 additions: LookupResult, LookupRowRecord. The rest of the §2 types
(MIDBridge, LibraryToken, DiffResult, etc.) land in Day 4+ when the code
consuming them lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Literal


class SchemaClass(Enum):
    """One of five bench-verified schema classes (HLD §`.anno` schema registry)."""

    A = "A"  # v44.3, v50.0; has Index col; "Version ID" at col 2
    B = "B"  # v52.2; has Index col; "Genetic ID" at col 2
    C = "C"  # v54.1; Index dropped; "Genetic ID" at col 1
    D = "D"  # v62.0; same as C with cols added back
    E = "E"  # v66.0; Master ID renamed to Individual ID; new Persistent Genetic ID col 2


class ExitCode(IntEnum):
    """Stable across versions per HLD §Exit codes."""

    OK = 0
    VALIDATION_FAILURE = 1
    IO_FAILURE = 2
    INVARIANT_VIOLATION = 3
    USAGE_ERROR = 4


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """One canonical field's location within a schema class.

    Per LLD §2.3.
    """

    canonical_name: str
    column: int  # 1-indexed column position
    normalized_header: str
    display_header: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaClassDef:
    """One schema class loaded from its YAML."""

    class_id: SchemaClass
    applies_to: tuple[str, ...]
    n_columns_set: tuple[int, ...]
    detection_signature: tuple[str, str]
    fields: dict[str, FieldMapping]
    notes: tuple[str, ...]
    not_present: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaClassDef:
        """Parse a YAML-loaded dict into the dataclass.

        Validates required keys; raises ValueError on missing or malformed
        structure (caller wraps in InvariantViolation for a clean exit code)."""
        try:
            class_id = SchemaClass(data["class_id"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"malformed schema YAML: bad class_id: {e}") from e

        applies_to = tuple(data.get("applies_to", []))

        n_cols_raw = data.get("n_columns")
        if n_cols_raw is None:
            raise ValueError(f"schema YAML for class {class_id.value} missing n_columns")
        n_columns_set: tuple[int, ...] = (
            (n_cols_raw,) if isinstance(n_cols_raw, int) else tuple(int(x) for x in n_cols_raw)
        )

        sig = data.get("detection_signature", {})
        detection_signature = (
            str(sig.get("col_0_normalized", "")),
            str(sig.get("col_1_normalized", "")),
        )

        fields_raw = data.get("fields", {})
        fields: dict[str, FieldMapping] = {}
        for canonical, mapping in fields_raw.items():
            fields[canonical] = FieldMapping(
                canonical_name=canonical,
                column=int(mapping["column"]),
                normalized_header=str(mapping["normalized_header"]),
                display_header=mapping.get("display_header"),
            )

        notes = tuple(data.get("notes", []))
        not_present = tuple(data.get("not_present", []))

        return cls(
            class_id=class_id,
            applies_to=applies_to,
            n_columns_set=n_columns_set,
            detection_signature=detection_signature,
            fields=fields,
            notes=notes,
            not_present=not_present,
        )

    def has_field(self, canonical: str) -> bool:
        return canonical in self.fields

    def column_for(self, canonical: str) -> int:
        """Return the 1-indexed column position for a canonical field.

        Raises MissingNativeFieldError (deferred import) if absent.
        """
        if canonical not in self.fields:
            from .errors import MissingNativeFieldError

            raise MissingNativeFieldError(
                f"field {canonical!r} not present in schema class "
                f"{self.class_id.value} (applies to {list(self.applies_to)})"
            )
        return self.fields[canonical].column


# === Day-4: MID-rename bridge types ===


@dataclass(frozen=True, slots=True)
class MIDRenameEvent:
    """One detected (or manually-supplied) Master/Individual-ID rename across
    two versions. Per LLD §2.5.

    `via_genetic_id` is the shared Genetic ID that triggered the auto-detection;
    None for manually-supplied bridge entries."""

    v_old_label: str
    mid_old: str
    v_new_label: str
    mid_new: str
    via_genetic_id: str | None = None


@dataclass
class MIDBridge:
    """Auto-detected MID renames + manual override entries.

    O(1) cross-version canonical-id lookup via the _fwd index. The canonical
    version is the latest version among supplied .anno files; the canonical
    id for an individual is its MID in that latest version."""

    events: list[MIDRenameEvent] = field(default_factory=list)
    # (version_label, mid) -> canonical mid (the latest-version MID for the chain).
    _fwd: dict[tuple[str, str], str] = field(default_factory=dict, repr=False, compare=False)
    # canonical_mid -> set of (version_label, mid_in_that_version) pairs.
    _rev: dict[str, set[tuple[str, str]]] = field(default_factory=dict, repr=False, compare=False)
    canonical_version: str = ""

    def canonical_id(self, version_label: str, mid: str) -> str:
        """Translate (version_label, mid) to the canonical individual_id.

        Unknown (version, mid) pairs fall through to the input mid itself —
        the individual exists in only one supplied version and is its own
        canonical id."""
        return self._fwd.get((version_label, mid), mid)

    def events_for(self, version_label: str, mid: str) -> list[MIDRenameEvent]:
        """Return all rename events whose chain includes (version_label, mid).

        Used by the lookup renderer to populate LookupResult.master_id_bridge."""
        canonical = self.canonical_id(version_label, mid)
        return [
            e
            for e in self.events
            if self._fwd.get((e.v_old_label, e.mid_old)) == canonical
            and self._fwd.get((e.v_new_label, e.mid_new)) == canonical
        ]


# === Day-3: lookup result types ===


@dataclass(frozen=True, slots=True)
class LookupRowRecord:
    """One row's data in a lookup result. Per LLD §2.10."""

    version_label: str
    genetic_id: str
    group_id: str
    snps_hit_1240k: int | None
    persistent_genetic_id: int | None  # class E only; None for A–D


@dataclass
class LookupResult:
    """Output of `aadr-resolve lookup`. Per LLD §2.10 / HLD §Output: lookup."""

    query: str
    individual_id_canonical: str  # equals query if no bridge; query's canonical post-Day-4
    matched_via: Literal["individual_id", "genetic_id", "not_found"]
    # Day-4 will populate this from MID-bridge events; Day-3 leaves empty.
    master_id_bridge: list[dict[str, str]] = field(default_factory=list)
    per_version: dict[str, list[LookupRowRecord]] = field(default_factory=dict)
    status_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view of the result."""
        return {
            "query": self.query,
            "individual_id_canonical": self.individual_id_canonical,
            "matched_via": self.matched_via,
            "master_id_bridge": list(self.master_id_bridge),
            "per_version": {
                v: [
                    {
                        "version_label": r.version_label,
                        "genetic_id": r.genetic_id,
                        "group_id": r.group_id,
                        "snps_hit_1240k": r.snps_hit_1240k,
                        "persistent_genetic_id": r.persistent_genetic_id,
                    }
                    for r in rows
                ]
                for v, rows in self.per_version.items()
            },
            "status_flags": list(self.status_flags),
        }

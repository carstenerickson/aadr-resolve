"""Shared dataclasses and enums.

Per LLD §2. Day-1 scope: SchemaClass, ExitCode, FieldMapping, SchemaClassDef.
The rest of the §2 types (MIDBridge, LibraryToken, DiffResult, etc.) land in
Day 2+ when the code consuming them lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any


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

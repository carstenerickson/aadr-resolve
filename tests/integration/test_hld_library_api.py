"""HLD test 29: AnnoFrame.from_path smoke — library API surface.

Tests 30–32 (SchemaDetectionError, resolve_master_ids, resolve_genetic_ids)
land in Day 4 alongside the MID-rename bridge module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.types import SchemaClass


def test_annoframe_from_path_smoke(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """HLD test 29: AnnoFrame.from_path(v66_path) returns an instance with
    correct .schema_class, .n_rows, and the expected typed-accessor dtypes
    (string identity / Int64 nullable date / Float64 coverage)."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.E])
    assert af.schema_class == SchemaClass.E
    assert af.schema_def.class_id == SchemaClass.E
    assert af.n_rows == 50
    # Identity accessors are string-dtype.
    assert str(af.genetic_id.dtype) == "string"
    assert str(af.individual_id.dtype) == "string"
    assert str(af.group_id.dtype) == "string"
    # Date is Int64 nullable.
    assert str(af.date_calbp.dtype) == "Int64"
    # Coverage is Float64.
    assert str(af.coverage.dtype) == "Float64"
    # PGID is Int64 nullable for class E (None for A–D).
    pgid = af.persistent_genetic_id
    assert pgid is not None
    assert str(pgid.dtype) == "Int64"


def test_annoframe_pgid_none_for_classes_a_through_d(
    tiny_anno_paths: dict[SchemaClass, Path],
) -> None:
    """The per-row numeric PGID column is v66+ only."""
    for cls in (SchemaClass.A, SchemaClass.B, SchemaClass.C, SchemaClass.D):
        af = AnnoFrame.from_path(tiny_anno_paths[cls])
        assert af.persistent_genetic_id is None, (
            f"class {cls.value} should NOT have persistent_genetic_id"
        )


def test_annoframe_typed_accessors_return_copies(
    tiny_anno_paths: dict[SchemaClass, Path],
) -> None:
    """Per LLD §2.4: typed accessors return COPIES so library consumers
    can mutate without corrupting AnnoFrame's internal state."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.E])
    first = af.genetic_id
    second = af.genetic_id
    # Mutating `first` doesn't affect `second`.
    first.iloc[0] = "MUTATED"
    assert second.iloc[0] != "MUTATED"


def test_annoframe_schema_detection_error(tmp_path: Path) -> None:
    """HLD test 30: AnnoFrame.from_path on a .anno with unknown header signature
    raises SchemaDetectionError carrying observed + known signatures;
    schema_override='E' bypasses the failure."""
    from aadr_resolve.errors import SchemaDetectionError

    # Build a synthetic .anno with a totally unknown header signature.
    bogus = tmp_path / "bogus.anno"
    bogus.write_text(
        "unknown_col_0\tunknown_col_1\textra_col_2\ndata0\tdata1\tdata2\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaDetectionError) as exc_info:
        AnnoFrame.from_path(bogus)
    err = exc_info.value
    assert err.observed[0] == 3  # ncols
    assert err.observed[1] == "unknown_col_0"
    assert err.observed[2] == "unknown_col_1"
    assert err.known  # at least one registered signature
    assert "--schema-override" in str(err)

    # schema_override='E' bypasses the dispatch check.
    af = AnnoFrame.from_path(bogus, schema_override=SchemaClass.E)
    assert af.schema_class == SchemaClass.E


def test_annoframe_repr_concise(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """__repr__ doesn't dump the DataFrame; gives a single line."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.E])
    s = repr(af)
    assert "AnnoFrame" in s
    assert "schema_class='E'" in s
    assert "n_rows=50" in s
    assert "\n" not in s

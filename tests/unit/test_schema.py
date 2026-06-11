"""HLD tests 1-5 (schema discovery) + a few LLD-level unit tests.

Per the LLD §5.3 mapping. Each `test_schema_class_<X>` test loads the
synthetic mini-fixture for class X via AnnoFrame.from_path and asserts:
  - The detected schema class matches.
  - The column-to-canonical-field map matches the YAML.
  - The detection signature matches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.errors import SchemaDetectionError
from aadr_resolve.schema import (
    detect_class,
    display_normalize,
    load_all_schemas,
    load_schema,
    normalize_header,
)
from aadr_resolve.types import SchemaClass, SchemaClassDef

# === HLD tests 1-5 (one per class) ===


def _assert_class_resolves(tiny_path: Path, expected: SchemaClass) -> AnnoFrame:
    """Load + assert the schema class. Returns the loaded AnnoFrame for follow-up checks."""
    af = AnnoFrame.from_path(tiny_path)
    assert af.schema_class == expected, (
        f"expected class {expected.value}, got {af.schema_class.value}"
    )
    return af


def test_schema_class_A(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """HLD test 1: v44.3 + v50.0 → class A."""
    af = _assert_class_resolves(tiny_anno_paths[SchemaClass.A], SchemaClass.A)
    yaml_def = load_schema(SchemaClass.A)
    # YAML's field map matches what AnnoFrame surfaces.
    assert af.schema_def.fields["genetic_id"].column == yaml_def.fields["genetic_id"].column
    assert af.schema_def.fields["individual_id"].column == yaml_def.fields["individual_id"].column
    assert af.schema_def.fields["group_id"].column == yaml_def.fields["group_id"].column
    # Class A's detection signature: (index, version_id).
    assert af.schema_def.detection_signature == ("index", "version_id")


def test_schema_class_B(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """HLD test 2: v52.2 → class B."""
    af = _assert_class_resolves(tiny_anno_paths[SchemaClass.B], SchemaClass.B)
    assert af.schema_def.detection_signature == ("index", "genetic_id")


def test_schema_class_C(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """HLD test 3: v54.1 → class C."""
    af = _assert_class_resolves(tiny_anno_paths[SchemaClass.C], SchemaClass.C)
    assert af.schema_def.detection_signature == ("genetic_id", "master_id")
    assert af.n_columns == 36


def test_schema_class_C_35_col_trailing_tab(
    tiny_anno_paths: dict[SchemaClass, Path], tmp_path: Path
) -> None:
    """Published v54.1 `.anno` files (both 1240K and HO) carry a trailing tab, so
    the loader drops the phantom column and detection sees 35 — which must still
    resolve to class C. The synthetic class-C fixture fills its trailing column, so
    it never exercised this path and real public files failed to load with
    SchemaDetectionError (class C accepted n_columns 36 only)."""
    rows = tiny_anno_paths[SchemaClass.C].read_text().splitlines()
    # Empty the (unmapped) trailing column → 35 mapped columns + a trailing tab,
    # exactly the shape of the published v54.1 files.
    public = "\n".join("\t".join([*r.split("\t")[:-1], ""]) for r in rows) + "\n"
    p = tmp_path / "v54.1_public_trailing_tab.anno"
    p.write_text(public)

    af = AnnoFrame.from_path(p)
    assert af.schema_class == SchemaClass.C
    assert af.n_columns == 35  # trailing-tab phantom dropped
    # Every mapped field still extracts from its class-C position.
    assert af.genetic_id.notna().any()
    assert af.group_id.notna().any()
    assert af.date_calbp.notna().any()


def test_schema_class_D(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """HLD test 4: v62.0 → class D."""
    af = _assert_class_resolves(tiny_anno_paths[SchemaClass.D], SchemaClass.D)
    assert af.schema_def.detection_signature == ("genetic_id", "master_id")
    assert af.n_columns == 42


def test_schema_class_E(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """HLD test 5: v66.0 → class E.

    Plus: persistent_genetic_id is mapped; individual_id (not master_id)
    is the join-key canonical name."""
    af = _assert_class_resolves(tiny_anno_paths[SchemaClass.E], SchemaClass.E)
    assert af.schema_def.detection_signature == ("genetic_id", "persistent_genetic_id")
    assert "persistent_genetic_id" in af.schema_def.fields
    assert "individual_id" in af.schema_def.fields
    # Class E renamed Master ID → Individual ID at col 3.
    assert af.schema_def.fields["individual_id"].column == 3
    # PGID accessor returns a Series (Day-1 raw-string scaffold; Int64 in Day 2).
    assert af.persistent_genetic_id is not None
    assert len(af.persistent_genetic_id) == af.n_rows


# === LLD-level unit tests ===


def test_normalize_header_simple() -> None:
    """The HLD-pinned regex normalization."""
    assert normalize_header("Genetic ID") == "genetic_id"
    assert normalize_header("Master ID") == "master_id"
    assert normalize_header("GroupID") == "groupid"


def test_normalize_header_strips_brackets_and_punctuation() -> None:
    """v62/v66 col 1 has a 600-byte inline-doc paragraph; the strip-at-first-bracket
    rule handles it."""
    raw = 'Genetic ID (suffices: ".DG" is high coverage shotgun, ".AG" is Agilent capture)'
    assert normalize_header(raw) == "genetic_id"


def test_normalize_header_strips_at_semicolon_and_colon() -> None:
    """Date method header uses ';' as the doc-string delimiter."""
    assert (
        normalize_header("Method for Determining Date; unless otherwise specified")
        == "method_for_determining_date"
    )
    assert (
        normalize_header("ASSESSMENT WARNINGS: X contamination interval") == "assessment_warnings"
    )


def test_display_normalize_preserves_human_form() -> None:
    """display_normalize is for diagnostic output; preserves caps + spaces."""
    assert display_normalize("Genetic ID (.DG suffix...)") == "Genetic ID"
    assert (
        display_normalize("Method for Determining Date; oxcal v4") == "Method for Determining Date"
    )


def test_load_all_schemas_returns_five() -> None:
    schemas = load_all_schemas()
    assert set(schemas.keys()) == set(SchemaClass)
    assert all(isinstance(v, SchemaClassDef) for v in schemas.values())


def test_load_all_schemas_signature_uniqueness(schemas: dict[SchemaClass, SchemaClassDef]) -> None:
    """The signature-uniqueness invariant in load_all_schemas catches duplicate
    (ncols, col0_norm, col1_norm) tuples across classes."""
    seen: set[tuple[int, str, str]] = set()
    for defn in schemas.values():
        for ncols in defn.n_columns_set:
            sig = (ncols, *defn.detection_signature)
            assert sig not in seen, f"duplicate signature {sig}"
            seen.add(sig)


def test_detect_class_with_override_skips_signature_check(
    schemas: dict[SchemaClass, SchemaClassDef],
) -> None:
    """--schema-override CLASS bypasses signature dispatch."""
    bogus = ["weird col 0", "weird col 1", "more cols"]
    defn = detect_class(bogus, schemas, override=SchemaClass.B)
    assert defn.class_id == SchemaClass.B


def test_detect_class_unknown_raises(schemas: dict[SchemaClass, SchemaClassDef]) -> None:
    """Unrecognized signature raises SchemaDetectionError with observed + known."""
    bogus = ["something", "totally_different_signature"] + [f"col_{i}" for i in range(40)]
    with pytest.raises(SchemaDetectionError) as exc_info:
        detect_class(bogus, schemas)
    # Error message includes the suggested override.
    assert "--schema-override" in str(exc_info.value)


def test_detect_class_short_header_raises(schemas: dict[SchemaClass, SchemaClassDef]) -> None:
    """A header with <2 columns can't possibly match; should raise cleanly."""
    with pytest.raises(SchemaDetectionError):
        detect_class(["only_one_column"], schemas)


def test_annoframe_to_dict_summary(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """AnnoFrame.to_dict() returns the schema-subcommand JSON shape."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.E])
    summary = af.to_dict()
    assert summary["schema_class"] == "E"
    assert summary["n_rows"] == 50
    assert "fields" in summary
    assert "genetic_id" in summary["fields"]
    assert summary["fields"]["genetic_id"]["column"] == 1

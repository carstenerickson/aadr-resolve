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

from aadr_resolve.annoframe import AnnoFrame, ensure_unique_versions
from aadr_resolve.errors import InvariantViolation, SchemaDetectionError, UsageError
from aadr_resolve.schema import (
    detect_class,
    display_normalize,
    load_all_schemas,
    load_schema,
    normalize_header,
    validate_version_overrides,
)
from aadr_resolve.types import SchemaClass, SchemaClassDef


def _mini_class_def(version_overrides: dict[str, dict[str, int]]) -> SchemaClassDef:
    """A minimal class-A-signature SchemaClassDef carrying the given overrides,
    for exercising override resolution/validation without a real YAML."""
    return SchemaClassDef.from_dict(
        {
            "class_id": "A",
            "n_columns": 44,
            "detection_signature": {"col_0_normalized": "index", "col_1_normalized": "version_id"},
            "fields": {"date_mean_bp": {"column": 10, "normalized_header": "date_mean_in_bp"}},
            "version_overrides": version_overrides,
        }
    )


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


def test_schema_class_F(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """Early Human Origins (v44.3, v50.0) → class F: an 18-column minimal schema
    with HO-specific header names ('Group Label')."""
    af = _assert_class_resolves(tiny_anno_paths[SchemaClass.F], SchemaClass.F)
    assert af.schema_def.detection_signature == ("index", "version_id")
    assert af.n_columns == 18
    assert af.schema_def.fields["group_id"].normalized_header == "group_label"
    # Positional extraction through AnnoFrame accessors (base v44.3 layout: the
    # fixture infers no version label) — guards a base-column off-by-one in class F.
    assert af.genetic_id.iloc[0] == "Synth0001.SG"  # col 2
    assert af.group_id.iloc[0] == "Synth_Test_Population"  # col 8
    assert af.date_calbp.iloc[0] == 1639  # col 6


def test_version_overrides_resolve_columns_per_version() -> None:
    """A field that moves between releases sharing a class resolves to the right
    column by version_label. Class A: v50.0 drops v44.3's 'Representative contact',
    shifting the dates left one column. Class F (HO): the same shift."""
    a = load_schema(SchemaClass.A)
    assert a.column_for("date_mean_bp", version="v44.3") == 10  # base layout
    assert a.column_for("date_mean_bp", version="v50.0") == 9  # override
    assert a.column_for("date_mean_bp", version="v50.0.p1") == 9  # patch matches key
    assert a.column_for("date_mean_bp", version=None) == 10  # unknown → base
    # date_method shifts with the dates (9 → 8); without its override it would
    # collide on col 9 with date_mean_bp and read 'Date mean' instead of the method.
    assert a.column_for("date_method", version="v44.3") == 9  # base layout
    assert a.column_for("date_method", version="v50.0") == 8  # override
    assert a.column_for("date_sd_bp", version="v50.0") == 10  # whole band moves
    assert a.column_for("full_date", version="v50.0") == 11
    # No two fields may resolve to the same column for v50.0 (collision guard).
    v50_cols = [a.column_for(name, version="v50.0") for name in a.fields]
    assert len(v50_cols) == len(set(v50_cols))
    f = load_schema(SchemaClass.F)
    assert f.column_for("group_id", version="v44.3") == 8
    assert f.column_for("group_id", version="v50.0") == 7


def test_version_override_applied_at_extraction(tmp_path: Path) -> None:
    """End-to-end: AnnoFrame passes version_label, so a v50.0-layout HO file reads
    group_id from col 7 and the date from col 5 — NOT the base (v44.3) col 8 / col 6,
    which would land on a different field."""
    hdr = [
        "Index",
        "Version ID",
        "Master ID",
        "Publication",
        "Date mean in BP",
        "Full Date",
        "Group Label",
        "Locality",
        "Country",
        "Lat.",
        "Long.",
        "Data source",
        "Coverage on autosomal targets",
        "SNPs hit on autosomal targets",
        "Sex",
        "Library type",
        "ASSESSMENT",
        "ASSESSMENT REASONING",
    ]
    row = [
        "0",
        "S1.SG",
        "S1",
        "Pub",
        "4844",
        "x",
        "RightGroup",
        "WrongIfBase",
        "Loc",
        "0",
        "0",
        "src",
        "1.2",
        "500000",
        "M",
        "ss",
        "PASS",
        "ok",
    ]
    p = tmp_path / "v50.0_HO_layout.anno"
    p.write_text("\t".join(hdr) + "\n" + "\t".join(row) + "\n")

    af = AnnoFrame.from_path(p, version_label="v50.0")
    assert af.schema_class == SchemaClass.F
    assert af.group_id.iloc[0] == "RightGroup"  # override col 7, not base col 8
    assert af.date_calbp.iloc[0] == 4844  # override col 5, not base col 6 ('x')


def test_version_override_class_a_v50_dates(tmp_path: Path) -> None:
    """Headline-bug guard, end-to-end: a v50.0 1240K file reads its date from
    col 9 (override), NOT col 10 — which holds the SD and is what class A's base
    v44.3 layout wrongly read (e.g. I0626_all resolved to date_calbp=173 instead
    of 3850)."""
    ncols = 44  # v50.0 1240K width
    header = ["Index", "Version ID"] + [f"col{i}" for i in range(3, ncols + 1)]
    row = [""] * ncols
    row[0] = "0"  # Index
    row[1] = "Sample1.SG"  # Version ID -> genetic_id (col 2)
    row[8] = "3850"  # col 9: v50.0 date_mean_bp (override)
    row[9] = "173"  # col 10: the date SD == class A's base date_mean_bp (the bug)
    p = tmp_path / "v50.0_1240K_layout.anno"
    p.write_text("\t".join(header) + "\n" + "\t".join(row) + "\n")

    af = AnnoFrame.from_path(p, version_label="v50.0")
    assert af.schema_class == SchemaClass.A
    assert af.date_calbp.iloc[0] == 3850  # override col 9, not base col 10 (173)


def test_duplicate_version_labels_rejected(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """Class A (1240K) and class F (HO) both apply to v50.0 and infer the same
    label. The N-frame version-keyed flows (cohort, lookup) must reject the
    collision rather than silently overwrite one panel's data. Two same-version
    frames are fine for positional 2-frame flows (diff/join), so the guard lives
    in ensure_unique_versions, not detect_bridge."""
    af_a = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.A], version_label="v50.0")
    af_f = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.F], version_label="v50.0")
    with pytest.raises(UsageError, match="share version label"):
        ensure_unique_versions([af_a, af_f])
    # Distinct versions pass through.
    af_c = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.C])
    ensure_unique_versions([af_a, af_c])
    # Same version + same class is the pre-existing degenerate case join/turnover
    # rely on — it is NOT rejected (only cross-class label collisions are).
    af_a2 = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.A], version_label="v50.0")
    ensure_unique_versions([af_a, af_a2])


def test_version_override_most_specific_key_wins() -> None:
    """When several override keys match a version label, the most specific
    (longest) key wins, independent of YAML/dict ordering."""
    for overrides in (
        {"v50": {"date_mean_bp": 8}, "v50.0": {"date_mean_bp": 9}},
        {"v50.0": {"date_mean_bp": 9}, "v50": {"date_mean_bp": 8}},  # reversed order
    ):
        defn = _mini_class_def(overrides)
        assert defn.column_for("date_mean_bp", version="v50.0") == 9  # both match → most specific
        assert defn.column_for("date_mean_bp", version="v50.3") == 8  # only 'v50' matches
        assert defn.column_for("date_mean_bp", version="v51.0") == 10  # neither → base


def test_validate_version_overrides_rejects_bad_entries() -> None:
    """Load-time validation catches hand-authoring mistakes that would otherwise
    be silently ignored at lookup time."""
    with pytest.raises(InvariantViolation, match="not in this class"):
        validate_version_overrides(_mini_class_def({"v50.0": {"no_such_field": 9}}))
    with pytest.raises(InvariantViolation, match="outside the valid column range"):
        validate_version_overrides(_mini_class_def({"v50.0": {"date_mean_bp": 99}}))
    # A valid override passes (no raise).
    validate_version_overrides(_mini_class_def({"v50.0": {"date_mean_bp": 9}}))


def test_validate_version_overrides_rejects_column_collisions() -> None:
    """Load-time validation catches an override that lands a field on a column
    already held by another field in the same version's resolved layout — the
    exact silent bug overrides exist to fix (v50.0 date_method had no override and
    shared col 9 with the date_mean_bp override)."""
    # Two fields at cols 8 and 9; an override drops date_mean_bp onto col 8 (where
    # date_method already sits and has no override) → collision.
    defn = SchemaClassDef.from_dict(
        {
            "class_id": "A",
            "n_columns": 44,
            "detection_signature": {
                "col_0_normalized": "index",
                "col_1_normalized": "version_id",
            },
            "fields": {
                "date_method": {"column": 8, "normalized_header": "method_for_determining_date"},
                "date_mean_bp": {"column": 9, "normalized_header": "date_mean_in_bp"},
            },
            "version_overrides": {"v50.0": {"date_mean_bp": 8}},
        }
    )
    with pytest.raises(InvariantViolation, match="both resolve to column 8"):
        validate_version_overrides(defn)
    # The real class-A YAML (date_method + dates all shifted) has no collision.
    validate_version_overrides(load_schema(SchemaClass.A))


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

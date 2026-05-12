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


def test_resolve_master_ids_v54_to_v62(fixtures_dir: Path) -> None:
    """HLD test 31: resolve_master_ids translates a list of IIDs from
    src_version to dst_version GeneticIDs through the MID-rename bridge.

    Day-3 fixture uses v54/v62 instead of v44/v66 (the HLD's text mentions
    v44->v66 but Day-1 lacks a v44 Loschbour fixture; v54/v62 is the same
    bridge mechanism)."""
    from aadr_resolve import resolve_master_ids

    result = resolve_master_ids(
        ids=["I0001", "Bichon", "Mota"],
        src_version="v54.1",
        dst_version="v62.0",
        anno_paths={
            "v54.1": fixtures_dir / "loschbour_v54.anno",
            "v62.0": fixtures_dir / "loschbour_v62.anno",
        },
    )
    # I0001 (v54) -> canonical Loschbour -> v62 has 3 GIDs for Loschbour;
    # alphabetically-first is 'I0001.AG'.
    assert result["I0001"] == "I0001.AG"
    # Bichon and Mota don't exist in either fixture; None.
    assert result["Bichon"] is None
    assert result["Mota"] is None


def test_resolve_genetic_ids_multi_row(fixtures_dir: Path) -> None:
    """HLD test 32: resolve_genetic_ids returns ALL matching v_new GIDs
    for the same individual (multi-row-per-IID preserved)."""
    from aadr_resolve import resolve_genetic_ids

    result = resolve_genetic_ids(
        ids=["I0001"],
        src_version="v54.1",
        dst_version="v62.0",
        anno_paths={
            "v54.1": fixtures_dir / "loschbour_v54.anno",
            "v62.0": fixtures_dir / "loschbour_v62.anno",
        },
    )
    # I0001 (v54 GID) -> canonical individual Loschbour -> v62's 3 GIDs.
    assert sorted(result["I0001"]) == ["I0001.AG", "Loschbour.DG", "Loschbour_snpAD.DG"]


def test_resolve_master_ids_with_anno_frames(fixtures_dir: Path) -> None:
    """The anno_frames= path avoids re-loading."""
    from aadr_resolve import AnnoFrame, resolve_master_ids

    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    result = resolve_master_ids(
        ids=["I0001"],
        src_version="v54.1",
        dst_version="v62.0",
        anno_frames={"v54.1": af_v54, "v62.0": af_v62},
    )
    assert result["I0001"] == "I0001.AG"


def test_resolve_requires_both_versions(fixtures_dir: Path) -> None:
    """Missing version_label in anno_paths raises KeyError."""
    from aadr_resolve import resolve_master_ids

    with pytest.raises(KeyError):
        resolve_master_ids(
            ids=["I0001"],
            src_version="v54.1",
            dst_version="v62.0",
            anno_paths={"v54.1": fixtures_dir / "loschbour_v54.anno"},
        )


def test_resolve_requires_inputs() -> None:
    """Neither anno_paths nor anno_frames raises ValueError."""
    from aadr_resolve import resolve_master_ids

    with pytest.raises(ValueError, match="either anno_paths or anno_frames"):
        resolve_master_ids(ids=["I0001"], src_version="v54.1", dst_version="v62.0")


def test_aadr_subset_contract_q1_exception_classes_importable() -> None:
    """aadr-subset Q1: exception classes for catch-and-rethrow are
    importable from the top-level `aadr_resolve` namespace. Specifically
    `CollisionDetected` (cross-lab MID collision) and the broader
    hierarchy aadr-subset wraps."""
    from aadr_resolve import (
        AadrResolveError,
        CollisionDetected,
        InvariantViolation,
        IOFailure,
        MissingNativeFieldError,
        SchemaDetectionError,
        UsageError,
        ValidationError,
    )

    # CollisionDetected is the canonical name for the cross-lab MID case
    # (HLD §MID rename detection). It subclasses InvariantViolation
    # (exit 3) so aadr-subset's "except aadr_resolve.CollisionDetected"
    # catches it AND "except aadr_resolve.InvariantViolation" catches
    # the wider class.
    assert issubclass(CollisionDetected, InvariantViolation)
    assert issubclass(InvariantViolation, AadrResolveError)
    assert issubclass(SchemaDetectionError, InvariantViolation)
    assert issubclass(MissingNativeFieldError, InvariantViolation)
    # ValidationError + IOFailure + UsageError are sibling AadrResolveError
    # subclasses (not InvariantViolation).
    assert issubclass(ValidationError, AadrResolveError)
    assert issubclass(IOFailure, AadrResolveError)
    assert issubclass(UsageError, AadrResolveError)


def test_aadr_subset_contract_q2_mid_bridge_kwarg(fixtures_dir: Path, tmp_path: Path) -> None:
    """aadr-subset Q2: resolve_master_ids accepts mid_bridge=PATH kwarg.

    Canonical name is `mid_bridge` (matches aadr-subset's defensive
    code). Loads + merges per HLD §MID rename detection semantics."""
    from aadr_resolve import resolve_master_ids

    # Write a manual bridge that redirects I0001 -> FakeID, overriding the
    # auto-detected I0001 -> Loschbour.
    override = tmp_path / "manual.tsv"
    override.write_text(
        "v_old_label\tmid_old\tv_new_label\tmid_new\nv54.1\tI0001\tv62.0\tFakeID\n",
        encoding="utf-8",
    )

    # Without override: auto-detection gives Loschbour as canonical.
    auto_result = resolve_master_ids(
        ids=["I0001"],
        src_version="v54.1",
        dst_version="v62.0",
        anno_paths={
            "v54.1": fixtures_dir / "loschbour_v54.anno",
            "v62.0": fixtures_dir / "loschbour_v62.anno",
        },
    )
    # I0001 (v54) -> canonical Loschbour -> v62 GIDs.
    assert auto_result["I0001"] == "I0001.AG"

    # With override: I0001 -> FakeID, which doesn't exist in v62, so None.
    overridden = resolve_master_ids(
        ids=["I0001"],
        src_version="v54.1",
        dst_version="v62.0",
        anno_paths={
            "v54.1": fixtures_dir / "loschbour_v54.anno",
            "v62.0": fixtures_dir / "loschbour_v62.anno",
        },
        mid_bridge=override,
    )
    # The override redirects canonical to "FakeID" which isn't an IID
    # in v62; resolve returns None.
    assert overridden["I0001"] is None


def test_aadr_subset_contract_q2_resolve_genetic_ids_mid_bridge(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """resolve_genetic_ids accepts the same `mid_bridge` kwarg."""
    from aadr_resolve import resolve_genetic_ids

    override = tmp_path / "manual.tsv"
    override.write_text(
        "v_old_label\tmid_old\tv_new_label\tmid_new\nv54.1\tI0001\tv62.0\tFakeID\n",
        encoding="utf-8",
    )

    result = resolve_genetic_ids(
        ids=["I0001"],
        src_version="v54.1",
        dst_version="v62.0",
        anno_paths={
            "v54.1": fixtures_dir / "loschbour_v54.anno",
            "v62.0": fixtures_dir / "loschbour_v62.anno",
        },
        mid_bridge=override,
    )
    # I0001 -> FakeID; no v62 rows match FakeID; empty list.
    assert result["I0001"] == []


def test_aadr_subset_contract_q9_annoframe_path(
    tiny_anno_paths: dict[SchemaClass, Path],
) -> None:
    """aadr-subset Q9: AnnoFrame.path is populated by the loader. Allows
    sibling tools to pass anno_paths={} to resolve_master_ids using the
    same AnnoFrame they already loaded."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.E])
    assert af.path is not None
    assert af.path == tiny_anno_paths[SchemaClass.E]
    # An AnnoFrame constructed without going through from_path() has path=None.
    af_direct = AnnoFrame(
        version="v66.0",
        schema_class=af.schema_class,
        schema_def=af.schema_def,
        df=af.df,
    )
    assert af_direct.path is None


def test_annoframe_repr_concise(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    """__repr__ doesn't dump the DataFrame; gives a single line."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.E])
    s = repr(af)
    assert "AnnoFrame" in s
    assert "schema_class='E'" in s
    assert "n_rows=50" in s
    assert "\n" not in s

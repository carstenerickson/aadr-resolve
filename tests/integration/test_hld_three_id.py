"""HLD tests 9 + 10: three-ID data model + within-version multi-row IID."""

from __future__ import annotations

from pathlib import Path

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.lookup import lookup_single
from aadr_resolve.types import SchemaClass


def test_v66_three_id_columns(fixtures_dir: Path) -> None:
    """HLD test 9: v66 input exposes genetic_id, individual_id, and
    persistent_genetic_id distinctly. Loschbour resolves to 2 rows under
    IID `Loschbour` with GIDs Loschbour.AG (PGID 33) and Loschbour.DG
    (PGID 39136)."""
    af = AnnoFrame.from_path(fixtures_dir / "loschbour_v66.anno")
    assert af.schema_class == SchemaClass.E

    # All three ID columns surface as distinct Series.
    gids = set(af.genetic_id.dropna())
    iids = set(af.individual_id.dropna())
    assert "Loschbour.AG" in gids
    assert "Loschbour.DG" in gids
    assert "Loschbour" in iids
    # Individual ID and Genetic ID are NOT the same value-set for Loschbour.
    assert "Loschbour" not in gids
    assert "Loschbour.AG" not in iids

    # Persistent Genetic ID is per-row Int64; Loschbour's two rows have
    # different PGIDs.
    pgid_series = af.persistent_genetic_id
    assert pgid_series is not None
    iid_series = af.individual_id
    loschbour_pgids = pgid_series[iid_series == "Loschbour"].tolist()
    assert sorted(int(p) for p in loschbour_pgids) == [33, 39136]


def test_within_version_multi_row_per_iid_is_normal(fixtures_dir: Path) -> None:
    """HLD test 10: UKY001 has 7 rows in v62. Loader returns all of them
    without complaint; lookup surfaces all 7 in per_version['v62.0']."""
    af = AnnoFrame.from_path(fixtures_dir / "uky001_v62.anno")
    iid_mask = af.individual_id == "UKY001"
    assert iid_mask.sum() == 7

    # All 7 GIDs distinct (different suffix/data-type combos).
    uky001_gids = af.genetic_id[iid_mask].tolist()
    assert len(uky001_gids) == 7
    assert len(set(uky001_gids)) == 7

    # lookup() surfaces all 7 as one per_version entry.
    result = lookup_single("UKY001", [af])
    assert result.matched_via == "individual_id"
    assert result.individual_id_canonical == "UKY001"
    assert af.version in result.per_version
    rows = result.per_version[af.version]
    assert len(rows) == 7
    assert "multi_row" in result.status_flags


def test_lookup_loschbour_returns_two_rows(fixtures_dir: Path) -> None:
    """End-to-end lookup smoke: Loschbour in v66 returns 2 rows
    (.AG + .DG). The multi-library invariant from HLD §Library-token."""
    af = AnnoFrame.from_path(fixtures_dir / "loschbour_v66.anno")
    result = lookup_single("Loschbour", [af])
    assert result.matched_via == "individual_id"
    assert af.version in result.per_version
    rows = result.per_version[af.version]
    assert len(rows) == 2
    gids = {r.genetic_id for r in rows}
    assert gids == {"Loschbour.AG", "Loschbour.DG"}
    # PGIDs surface on each row.
    pgids = {r.persistent_genetic_id for r in rows}
    assert pgids == {33, 39136}
    assert "multi_row" in result.status_flags


def test_lookup_fallback_to_genetic_id(fixtures_dir: Path) -> None:
    """When the query doesn't match any individual_id, the resolver falls
    back to genetic_id matching (HLD §Output: lookup)."""
    af = AnnoFrame.from_path(fixtures_dir / "loschbour_v66.anno")
    # 'Loschbour.AG' is a GID, not an IID — the resolver should still find it.
    result = lookup_single("Loschbour.AG", [af])
    assert result.matched_via == "genetic_id"
    assert result.individual_id_canonical == "Loschbour"
    assert "matched_via_genetic_id" in result.status_flags
    # And it surfaces both Loschbour rows (.AG AND .DG) since canonical_id
    # is "Loschbour" and we collect all rows under that IID.
    rows = result.per_version[af.version]
    assert len(rows) == 2


def test_lookup_not_found(fixtures_dir: Path) -> None:
    """Query that matches neither IID nor GID."""
    af = AnnoFrame.from_path(fixtures_dir / "loschbour_v66.anno")
    result = lookup_single("NonexistentSample", [af])
    assert result.matched_via == "not_found"
    assert result.per_version == {}
    assert "not_found" in result.status_flags


def test_lookup_empty_anno_list_returns_not_found() -> None:
    """Defensive: lookup_single([]) shouldn't crash."""
    result = lookup_single("Loschbour", [])
    assert result.matched_via == "not_found"

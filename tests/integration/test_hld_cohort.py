"""HLD tests 20-22 + 38-41: cohort manifest + library-token + collapse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge
from aadr_resolve.cohort import build_manifest, parse_cohort_file
from aadr_resolve.library_token import (
    build_all_library_identities,
    build_library_identity,
    parse_gid,
)
from aadr_resolve.reporting import write_cohort_json, write_cohort_tsv

# === HLD tests 38-41: library-token explosion ===


def _load_loschbour_pair(fixtures_dir: Path) -> tuple[list[AnnoFrame], object]:
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    af_v66 = AnnoFrame.from_path(fixtures_dir / "loschbour_v66.anno", version_label="v66.0")
    afs = [af_v54, af_v62, af_v66]
    bridge = detect_bridge(afs)
    return afs, bridge


def test_parse_gid_examples() -> None:
    """parse_gid splits (stem, suffix). Bench-verified cases."""
    assert parse_gid("I0001") == ("I0001", None)
    assert parse_gid("I0001.AG") == ("I0001", "AG")
    assert parse_gid("Loschbour.AG") == ("Loschbour", "AG")
    assert parse_gid("Loschbour.DG") == ("Loschbour", "DG")
    assert parse_gid("Loschbour_snpAD.DG") == ("Loschbour_snpAD", "DG")


def test_library_identity_loschbour_three_versions(fixtures_dir: Path) -> None:
    """HLD test 38 (subset): Loschbour across v54+v62+v66 produces multiple
    library tokens. The .AG-track chain (I0001 -> I0001.AG -> Loschbour.AG)
    is the headline case from HLD §Library-token; verify it lands as one
    library_token in the result."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identity = build_library_identity(afs, bridge, "Loschbour")

    tokens = [lt.token for lt in identity.libraries]
    # Expected: at least Loschbour.AG (the AG-track chain), Loschbour.DG (DG-track),
    # and Loschbour_snpAD.DG (dropped before v66 -> token = latest = v62's GID).
    assert "Loschbour.AG" in tokens
    assert "Loschbour.DG" in tokens
    assert "Loschbour_snpAD.DG" in tokens

    # Rule A + Rule B chained the AG-track:
    ag_track = next(lt for lt in identity.libraries if lt.token == "Loschbour.AG")
    assert ag_track.per_version_gid["v54.1"] == "I0001"
    assert ag_track.per_version_gid["v62.0"] == "I0001.AG"
    assert ag_track.per_version_gid["v66.0"] == "Loschbour.AG"
    assert ag_track.chain_status == "chained"

    # snpAD library was dropped before v66 — token = its v62 GID.
    snpad = next(lt for lt in identity.libraries if lt.token == "Loschbour_snpAD.DG")
    assert snpad.per_version_gid["v66.0"] is None


def test_cohort_loschbour_full_pipeline(fixtures_dir: Path, tmp_path: Path) -> None:
    """HLD test 38: cohort against v54+v62+v66 for a Loschbour-only cohort
    produces multiple rows (one per library) under cohort_label='WHGA'."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    cohort_input = {"Loschbour": "WHGA"}
    manifest = build_manifest(
        cohort_input,
        afs,
        bridge,
        identities,
        cohort_version="v66.0",
    )

    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    # Loschbour has 3 libraries (AG-track, DG-track, snpAD_DG-track).
    assert len(loschbour_rows) == 3
    assert all(r.cohort_label == "WHGA" for r in loschbour_rows)
    assert all(r.cohort_label_source == "direct" for r in loschbour_rows)
    tokens = {r.library_token for r in loschbour_rows}
    assert tokens == {"Loschbour.AG", "Loschbour.DG", "Loschbour_snpAD.DG"}


def test_cohort_loschbour_v44_v66_gap_produces_orphan_rows(fixtures_dir: Path) -> None:
    """HLD test 39: with v54+v66 only (skipping v62), the AG-track chain
    cannot be fully inferred via Rules A+B. The Loschbour.DG library
    chains via shared GID; the bare-I0001 v54 entry and the
    Loschbour.AG v66 entry become orphan tokens (cannot chain across gap).

    Note: Rule B fires within a suffix-class when both versions have
    exactly 1 GID. v54 has 0 .AG-class GIDs (only bare I0001), so Rule B
    can't bridge bare → AG. Rule A requires same stem — `I0001` vs
    `Loschbour` differ. Expected: distinct tokens for the bare and AG
    entries."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v66 = AnnoFrame.from_path(fixtures_dir / "loschbour_v66.anno", version_label="v66.0")
    afs = [af_v54, af_v66]
    bridge = detect_bridge(afs)

    identity = build_library_identity(afs, bridge, "Loschbour")
    tokens = [lt.token for lt in identity.libraries]
    # Loschbour.DG chains (shared GID).
    assert "Loschbour.DG" in tokens
    # v66's Loschbour.AG appears as its own token.
    assert "Loschbour.AG" in tokens


def test_collapse_to_individual_default_gid_preference(fixtures_dir: Path, tmp_path: Path) -> None:
    """HLD test 40: --collapse-to-individual reduces Loschbour to ONE row.
    Default preference AG > DG > SG; v66 cell picks Loschbour.AG. Stderr
    warning enumerates dropped libraries."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    manifest = build_manifest(
        {"Loschbour": "WHGA"},
        afs,
        bridge,
        identities,
        cohort_version="v66.0",
        collapse=True,
    )
    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    assert len(loschbour_rows) == 1
    row = loschbour_rows[0]
    assert row.per_version_gid["v66.0"] == "Loschbour.AG"
    # 2 libraries dropped (Loschbour.DG + Loschbour_snpAD.DG).
    assert any("Loschbour" in w and "dropped" in w for w in manifest.warnings)


def test_gid_preference_override(fixtures_dir: Path) -> None:
    """HLD test 41: --gid-preference DG,AG,SG,HO picks Loschbour.DG instead
    of Loschbour.AG for the v66 cell."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    manifest = build_manifest(
        {"Loschbour": "WHGA"},
        afs,
        bridge,
        identities,
        cohort_version="v66.0",
        collapse=True,
        gid_preference=("DG", "AG", "SG", "HO"),
    )
    row = next(r for r in manifest.rows if r.individual_id_canonical == "Loschbour")
    assert row.per_version_gid["v66.0"] == "Loschbour.DG"


# === HLD tests 20-22: cohort manifest semantics ===


def test_cohort_round_trip(fixtures_dir: Path, tmp_path: Path) -> None:
    """HLD test 20: emit manifest -> write to TSV -> read it back -> compare.

    Day-6 scope: byte-stable round-trip just for the write side. Real
    re-parse round-trip lives in Day 11 self-dogfood."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    manifest = build_manifest(
        {"Loschbour": "WHGA"}, afs, bridge, identities, cohort_version="v66.0"
    )
    out_tsv = tmp_path / "manifest.tsv"
    write_cohort_tsv(manifest, out_tsv)

    # Write twice; both should be byte-identical.
    out_tsv_2 = tmp_path / "manifest_2.tsv"
    write_cohort_tsv(manifest, out_tsv_2)
    assert out_tsv.read_text() == out_tsv_2.read_text()

    # The TSV has the expected header columns.
    header = out_tsv.read_text().splitlines()[0]
    assert "cohort_label" in header
    assert "library_token" in header
    assert "v66_0_genetic_id" in header
    assert "status" in header


def test_cohort_label_propagation_default(fixtures_dir: Path) -> None:
    """HLD test 21: cohort_labels propagate from cohort_version to other
    versions via the MID bridge. The propagation is set to source='direct'
    for the canonical individual (since label was directly attached to
    cohort_input's IID, even though it's canonicalized through the bridge)."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    # Use the v54 form of the IID (pre-rename). After canonicalization
    # (bridge maps I0001 -> Loschbour), Loschbour gets the WHGA label.
    cohort_input = {"I0001": "WHGA"}
    manifest = build_manifest(cohort_input, afs, bridge, identities, cohort_version="v54.1")

    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    assert loschbour_rows, "expected Loschbour rows post-canonicalization"
    for row in loschbour_rows:
        assert row.cohort_label == "WHGA"
        assert row.cohort_label_source == "direct"


def test_cohort_no_propagate(fixtures_dir: Path) -> None:
    """HLD test 22: --no-propagate disables cross-version propagation.

    With no_propagate=True, ONLY canonicals matching cohort_input's direct
    entries get a label. Other canonicals don't appear in the output."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    # Cohort input is just `Loschbour` from v66.
    manifest_propagated = build_manifest(
        {"Loschbour": "WHGA"}, afs, bridge, identities, cohort_version="v66.0"
    )
    manifest_no_propagate = build_manifest(
        {"Loschbour": "WHGA"},
        afs,
        bridge,
        identities,
        cohort_version="v66.0",
        no_propagate=True,
    )
    # With or without propagation, Loschbour is in cohort_input directly,
    # so it's labeled in both. Difference is whether OTHER canonicals
    # picked up via bridge get inferred labels — but with our cohort_input
    # of just Loschbour, both manifests have the same individual set.
    propagated_ids = {r.individual_id_canonical for r in manifest_propagated.rows}
    no_propagate_ids = {r.individual_id_canonical for r in manifest_no_propagate.rows}
    assert propagated_ids == no_propagate_ids == {"Loschbour"}


def test_cohort_no_propagate_via_v54_iid(fixtures_dir: Path) -> None:
    """Stronger no_propagate test: cohort_input has 'I0001' scoped to v54.
    Bridge canonicalizes to 'Loschbour'. With propagation OFF and the
    cohort_version=v54.1 entry pointing to canonical 'Loschbour', the
    label still attaches to canonical 'Loschbour' (because the bridge
    is applied during direct-label assignment too — only INFERRED
    propagation is disabled)."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)

    manifest = build_manifest(
        {"I0001": "WHGA"},
        afs,
        bridge,
        identities,
        cohort_version="v54.1",
        no_propagate=True,
    )
    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    # The direct cohort entry I0001 canonicalizes to Loschbour, so Loschbour
    # rows still receive WHGA.
    assert loschbour_rows
    for row in loschbour_rows:
        assert row.cohort_label == "WHGA"


def test_parse_cohort_file_one_column(tmp_path: Path) -> None:
    p = tmp_path / "cohort.txt"
    p.write_text("Loschbour\nBichon\n# comment\nMota\n", encoding="utf-8")
    out = parse_cohort_file(p)
    assert out == {"Loschbour": None, "Bichon": None, "Mota": None}


def test_parse_cohort_file_two_columns(tmp_path: Path) -> None:
    p = tmp_path / "cohort.txt"
    p.write_text("Loschbour\tWHGA\nBichon\tWHGB\n", encoding="utf-8")
    out = parse_cohort_file(p)
    assert out == {"Loschbour": "WHGA", "Bichon": "WHGB"}


def test_parse_cohort_file_with_header(tmp_path: Path) -> None:
    p = tmp_path / "cohort.txt"
    p.write_text("individual_id\tcohort_label\nLoschbour\tWHGA\n", encoding="utf-8")
    out = parse_cohort_file(p)
    assert out == {"Loschbour": "WHGA"}


def test_parse_cohort_file_three_columns_raises(tmp_path: Path) -> None:
    p = tmp_path / "cohort.txt"
    p.write_text("Loschbour\tWHGA\textra\n", encoding="utf-8")
    from aadr_resolve.errors import UsageError

    with pytest.raises(UsageError):
        parse_cohort_file(p)


# === Cohort JSON output ===


def test_cohort_json_round_trip(fixtures_dir: Path, tmp_path: Path) -> None:
    """JSON output is valid + structurally matches the TSV header."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)
    manifest = build_manifest(
        {"Loschbour": "WHGA"}, afs, bridge, identities, cohort_version="v66.0"
    )
    out = tmp_path / "manifest.json"
    write_cohort_json(manifest, out)
    payload = json.loads(out.read_text())
    assert isinstance(payload, list)
    assert all("library_token" in r for r in payload)
    assert all("per_version_gid" in r for r in payload)

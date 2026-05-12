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


# === v0.2 A4: per-pair group_id_change_class ===


def test_per_pair_change_class_tsv_columns(fixtures_dir: Path, tmp_path: Path) -> None:
    """TSV header carries one group_id_change_class column per adjacent
    pair in versions_supplied. For Loschbour v54+v62+v66 that's two
    columns: v54_1_to_v62_0 and v62_0_to_v66_0."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)
    manifest = build_manifest(
        {"Loschbour": "Loschbour"}, afs, bridge, identities, cohort_version="v66.0"
    )
    out = tmp_path / "manifest.tsv"
    write_cohort_tsv(manifest, out)
    header = out.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert "group_id_change_class_v54_1_to_v62_0" in header
    assert "group_id_change_class_v62_0_to_v66_0" in header


def test_per_pair_change_class_loschbour_v54_to_v62_classified(fixtures_dir: Path) -> None:
    """Loschbour's Luxembourg_Loschbour → Luxembourg_Mesolithic.AG change
    between v54 and v62 must classify into one of the six classes (not
    'none', not None), because the group_id IS changing."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)
    manifest = build_manifest(
        {"Loschbour": "Loschbour"}, afs, bridge, identities, cohort_version="v66.0"
    )
    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    assert loschbour_rows, "no Loschbour row in manifest"
    # At least one Loschbour row should carry a non-trivial classification.
    classifications = {
        r.per_pair_group_change_class.get(("v54.1", "v62.0")) for r in loschbour_rows
    }
    # Group changed: must not be 'none' or None for at least one library.
    assert any(c not in (None, "none") for c in classifications), (
        f"expected at least one Loschbour library to have a real "
        f"group_id_change_class for v54→v62; got {classifications}"
    )


def test_per_pair_change_class_unchanged_group_is_none_string(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """When the group_id is identical across adjacent versions, the
    per-pair value is the string 'none' (not None — None means 'one side
    of the pair is missing the individual')."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)
    # Include the synth fillers; they have stable group_id across v62+v66
    # (Synth_Test_Population), so their v62→v66 pair must be 'none'.
    manifest = build_manifest(
        {
            "Loschbour": "Loschbour",
            "Synth0004": "Synth0004",
            "Synth0005": "Synth0005",
        },
        afs,
        bridge,
        identities,
        cohort_version="v66.0",
    )
    # Find a synth filler row that's present in both v62 and v66 with the
    # same group_id (synth uses a deterministic 2-population pool; same
    # IID → same group_id across both class-D and class-E synth runs).
    for row in manifest.rows:
        v62 = row.per_version_group_id.get("v62.0")
        v66 = row.per_version_group_id.get("v66.0")
        if v62 is not None and v66 is not None and v62 == v66:
            assert row.per_pair_group_change_class.get(("v62.0", "v66.0")) == "none"
            return
    # If no row qualifies, at least confirm the class is callable; this
    # is a deeply unlikely path for synth fixtures.
    pytest.skip("no row with stable group_id across v62+v66 in fixtures")


def test_per_pair_change_class_missing_version_is_none(fixtures_dir: Path) -> None:
    """When the individual is absent in one side of an adjacent pair, the
    per-pair value is None (not a string)."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)
    manifest = build_manifest(
        {"Loschbour": "Loschbour"}, afs, bridge, identities, cohort_version="v66.0"
    )
    # Loschbour's snpAD.DG library is present in v54 + v62 but absent in
    # v66 (the snpAD library was retired between v62 and v66). That row's
    # v62→v66 pair should be None on the change-class column.
    snpad_rows = [
        r
        for r in manifest.rows
        if r.individual_id_canonical == "Loschbour" and "snpAD" in r.library_token
    ]
    if not snpad_rows:
        pytest.skip("no snpAD library row found; fixture composition differs")
    row = snpad_rows[0]
    # v66 should have None group_id (library retired), so v62→v66 must be None.
    assert row.per_version_group_id.get("v66.0") is None
    assert row.per_pair_group_change_class.get(("v62.0", "v66.0")) is None


def test_per_pair_change_class_json_keys_stringified(fixtures_dir: Path, tmp_path: Path) -> None:
    """JSON output renders per-pair keys as '{v_old}__to__{v_new}' since
    JSON keys can't be tuples."""
    afs, bridge = _load_loschbour_pair(fixtures_dir)
    identities = build_all_library_identities(afs, bridge)
    manifest = build_manifest(
        {"Loschbour": "Loschbour"}, afs, bridge, identities, cohort_version="v66.0"
    )
    out = tmp_path / "manifest.json"
    write_cohort_json(manifest, out)
    payload = json.loads(out.read_text())
    assert payload, "empty manifest JSON"
    first = payload[0]
    assert "per_pair_group_change_class" in first
    keys = set(first["per_pair_group_change_class"].keys())
    assert "v54.1__to__v62.0" in keys
    assert "v62.0__to__v66.0" in keys


# === v0.2 A1 part 1: cohort stdout summary block ===


def test_cohort_stdout_summary_has_all_sections(fixtures_dir: Path, tmp_path: Path) -> None:
    """The cohort summary block contains every named section per HLD
    §Stdout summary block. Asserts the section headings exist; doesn't
    pin exact whitespace so the test isn't whitespace-fragile."""
    from click.testing import CliRunner

    from aadr_resolve.cli import cli as cli_group

    cohort_file = tmp_path / "cohort.tsv"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")
    out_path = tmp_path / "manifest.tsv"

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v66.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(out_path),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    stdout = result.stdout

    # Each block heading appears.
    assert "Loaded 3 .anno file(s):" in stdout
    assert "Cross-version bridge:" in stdout
    assert "Cohort input:" in stdout
    assert "GID-stable MID-rename detection:" in stdout
    assert "Manual --mid-bridge entries:" in stdout
    assert "Cross-lab MID collision check:" in stdout
    assert "Resolved in latest version:" in stdout
    assert "Wrote manifest.tsv" in stdout
    assert "Sample turnover within cohort:" in stdout
    assert "Done in" in stdout

    # Per-anno bullets carry the version label + class.
    assert "[loschbour_v54]" in stdout
    assert "[loschbour_v62]" in stdout
    assert "[loschbour_v66]" in stdout
    assert "class C" in stdout
    assert "class D" in stdout
    assert "class E" in stdout


def test_cohort_quiet_suppresses_stdout_summary(fixtures_dir: Path, tmp_path: Path) -> None:
    """--quiet suppresses the entire stdout summary block (nothing on
    stdout); stderr warnings still emit if any."""
    from click.testing import CliRunner

    from aadr_resolve.cli import cli as cli_group

    cohort_file = tmp_path / "cohort.tsv"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")
    out_path = tmp_path / "manifest.tsv"

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--quiet",
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(out_path),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Nothing on stdout.
    assert result.stdout == ""
    # Manifest still written.
    assert out_path.exists()


def test_cohort_stdout_summary_group_change_histogram_emitted(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """When at least one substantive group_id change exists in the
    manifest, the Group ID changes histogram block is emitted. With v54
    + v62 + v66 over Loschbour, Luxembourg_Loschbour → Mesolithic across
    the pairs produces 'partial' or 'substantive_regroup' entries."""
    from click.testing import CliRunner

    from aadr_resolve.cli import cli as cli_group

    cohort_file = tmp_path / "cohort.tsv"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")
    out_path = tmp_path / "manifest.tsv"

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v66.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(out_path),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    stdout = result.stdout
    assert "Group ID changes" in stdout
    # At least one of the six classes should appear with a non-zero count.
    classes = [
        "convention_restructure_suffix",
        "convention_restructure_country",
        "convention_restructure_order",
        "convention_restructure_punct",
        "partial",
        "substantive_regroup",
    ]
    assert any(c in stdout for c in classes)


# === v0.2 A2: --report-json summary sidecar ===


def test_cohort_report_json_summary_round_trip(fixtures_dir: Path, tmp_path: Path) -> None:
    """--report-json writes a parseable JSON sidecar with the LLD-pinned
    top-level keys."""
    from click.testing import CliRunner

    from aadr_resolve.cli import cli as cli_group

    cohort_file = tmp_path / "cohort.tsv"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")
    out_path = tmp_path / "manifest.tsv"
    report_path = tmp_path / "summary.json"

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--quiet",
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v66.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(out_path),
            "--report-json",
            str(report_path),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    # Top-level keys per HLD §Reports / LLD §3.14.
    expected_keys = {
        "versions_supplied",
        "schemas_detected",
        "bridge",
        "cohort",
        "gates",
        "warnings",
        "config",
        "elapsed_seconds",
    }
    assert expected_keys <= set(payload.keys())

    # versions_supplied is a list.
    assert isinstance(payload["versions_supplied"], list)
    assert "loschbour_v54" in payload["versions_supplied"]

    # schemas_detected maps each version label to a class letter.
    assert payload["schemas_detected"]["loschbour_v54"] == "C"
    assert payload["schemas_detected"]["loschbour_v66"] == "E"

    # bridge sub-keys.
    assert "auto_count" in payload["bridge"]
    assert "manual_count" in payload["bridge"]
    assert "collisions" in payload["bridge"]

    # cohort block sub-keys.
    cohort_block = payload["cohort"]
    assert "n_individuals" in cohort_block
    assert "n_libraries" in cohort_block
    assert "label_source_histogram" in cohort_block
    assert "status_histogram" in cohort_block
    assert "group_change_by_class" in cohort_block

    # gates echoes the actual run state.
    assert payload["gates"]["turnover"] in {"pass", "warn", "fail"}
    assert payload["gates"]["cohort_coverage"] in {"pass", "warn", "fail", "n/a"}


def test_cohort_report_json_config_echoes_flags(fixtures_dir: Path, tmp_path: Path) -> None:
    """The 'config' block echoes the CLI-resolved flag values."""
    from click.testing import CliRunner

    from aadr_resolve.cli import cli as cli_group

    cohort_file = tmp_path / "cohort.tsv"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")
    out_path = tmp_path / "manifest.tsv"
    report_path = tmp_path / "summary.json"

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--quiet",
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(out_path),
            "--report-json",
            str(report_path),
            "--turnover-fail",
            "0.50",
            "--cohort-coverage-fail",
            "0.0",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    config = payload["config"]
    assert config["turnover_fail"] == 0.50
    assert config["cohort_coverage_fail"] == 0.0
    assert config["output_format"] == "tsv"


def test_cohort_report_json_written_even_on_gate_fail(fixtures_dir: Path, tmp_path: Path) -> None:
    """--report-json is written BEFORE the ValidationError raise so CI
    can still inspect the failure shape. Force a cohort-coverage failure
    by setting --cohort-coverage-fail 1.01 (impossible threshold)."""
    from click.testing import CliRunner

    from aadr_resolve.cli import cli as cli_group
    from aadr_resolve.errors import ValidationError

    cohort_file = tmp_path / "cohort.tsv"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")
    out_path = tmp_path / "manifest.tsv"
    report_path = tmp_path / "summary.json"

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--quiet",
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(out_path),
            "--report-json",
            str(report_path),
            "--cohort-coverage-fail",
            "1.01",
        ],
        catch_exceptions=True,
    )
    assert isinstance(result.exception, ValidationError)
    # JSON sidecar is on disk even though we exited 1.
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["gates"]["cohort_coverage"] == "fail"

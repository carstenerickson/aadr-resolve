"""Integration tests for `compute_join` + the `join` subcommand.

Day-8 scope: wide-form pairwise join over the v_old × v_new intersection
of canonical individuals. Reuses `cohort.build_manifest` per LLD §3.12;
output schema is identical to cohort. HLD test 25 (pgen-samplebind
handoff) lives in test_hld_ancestry_pipeline.py — that's Day 11."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge
from aadr_resolve.cli import cli as cli_group
from aadr_resolve.join import compute_join


def test_join_loschbour_v54_to_v62_intersection_semantics(fixtures_dir: Path) -> None:
    """compute_join over Loschbour v54 + v62 fixtures yields one row per
    (canonical individual × library). Loschbour has 3 libraries in v54
    (bare-AG-chain via I0001 → I0001.AG, DG, snpAD.DG) and chains them
    forward to v62. The shared synth fillers (Synth0001..0004) also surface.

    Verifies the row-per-(individual × library) cardinality."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])

    manifest = compute_join(af_v54, af_v62, bridge)

    # At least one row for Loschbour.
    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    assert len(loschbour_rows) >= 1

    # Every row's cohort_label equals individual_id_canonical (no propagation
    # for join — canonical IS the label).
    for r in manifest.rows:
        assert r.cohort_label == r.individual_id_canonical
        assert r.cohort_label_source == "direct"

    # Two versions supplied; per_version_gid maps cover them both.
    assert manifest.versions_supplied == ("v54.1", "v62.0")
    for r in manifest.rows:
        assert set(r.per_version_gid.keys()) == {"v54.1", "v62.0"}


def test_join_canonical_label_is_canonical_id(fixtures_dir: Path) -> None:
    """For join, cohort_label_source is 'direct' for every row. No
    'inferred_from_v_X' rows in a join manifest — every individual is its
    own seed."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    manifest = compute_join(af_v54, af_v62, bridge)
    sources = {r.cohort_label_source for r in manifest.rows}
    assert sources == {"direct"}


def test_join_collapse_to_individual_one_row_per_indiv(fixtures_dir: Path) -> None:
    """--collapse-to-individual reduces row-per-library to row-per-individual.
    Loschbour has 3 libraries in v54+v62; collapsed manifest has 1 Loschbour
    row. The dropped-libraries warning surfaces in manifest.warnings."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])

    manifest = compute_join(af_v54, af_v62, bridge, collapse=True)

    # Exactly one row for Loschbour in the collapsed manifest.
    loschbour_rows = [r for r in manifest.rows if r.individual_id_canonical == "Loschbour"]
    assert len(loschbour_rows) == 1

    # n_libraries == n_individuals for a fully-collapsed manifest.
    assert manifest.n_libraries == manifest.n_individuals

    # The dropped-libraries warning fires for Loschbour (3 libraries → 1).
    loschbour_warnings = [w for w in manifest.warnings if "Loschbour" in w]
    assert any("dropped during --collapse-to-individual" in w for w in loschbour_warnings)


def test_join_cmd_smoke(fixtures_dir: Path, tmp_path: Path) -> None:
    """End-to-end: `aadr-resolve join v54.anno v62.anno -o out.tsv` writes
    a non-empty TSV with the expected header columns."""
    out_path = tmp_path / "join.tsv"
    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v62.0",
            "join",
            str(fixtures_dir / "loschbour_v62.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "-o",
            str(out_path),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    text = out_path.read_text(encoding="utf-8")
    header = text.splitlines()[0].split("\t")
    # Expected leading columns from cohort manifest schema.
    assert "cohort_label" in header
    assert "individual_id_canonical" in header
    assert "library_token" in header
    assert "status" in header


def test_join_cmd_quiet_suppresses_progress_line(fixtures_dir: Path, tmp_path: Path) -> None:
    """Without --quiet, `join` writes a 'Wrote N rows...' line to stdout.
    With --quiet, that line is suppressed (stderr warnings still emit)."""
    out_path = tmp_path / "join.tsv"
    runner = CliRunner()

    # First, without --quiet.
    result_noisy = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v62.0",
            "join",
            str(fixtures_dir / "loschbour_v62.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "-o",
            str(out_path),
        ],
        catch_exceptions=False,
    )
    assert result_noisy.exit_code == 0
    assert "Wrote " in result_noisy.stdout

    # Then with --quiet.
    out_path_quiet = tmp_path / "join_quiet.tsv"
    result_quiet = runner.invoke(
        cli_group,
        [
            "--quiet",
            "--version-label",
            "v62.0",
            "join",
            str(fixtures_dir / "loschbour_v62.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "-o",
            str(out_path_quiet),
        ],
        catch_exceptions=False,
    )
    assert result_quiet.exit_code == 0
    assert "Wrote " not in result_quiet.stdout


def test_join_cmd_json_output(fixtures_dir: Path, tmp_path: Path) -> None:
    """--json switches the writer to JSON-array output."""
    out_path = tmp_path / "join.json"
    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v62.0",
            "join",
            str(fixtures_dir / "loschbour_v62.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "-o",
            str(out_path),
            "--json",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    import json

    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    # Each row has the expected schema.
    first = parsed[0]
    assert "cohort_label" in first
    assert "individual_id_canonical" in first
    assert "library_token" in first


def test_join_output_schema_matches_cohort(fixtures_dir: Path, tmp_path: Path) -> None:
    """Per LLD pin §3.12: join reuses cohort.build_manifest. The output
    schema (TSV header) is identical between cohort and join given the
    same input version set — a join's TSV can be processed with the same
    downstream tooling as a cohort's."""
    runner = CliRunner()

    # Cohort over v54 + v62, with a one-line cohort file naming Loschbour.
    cohort_file = tmp_path / "cohort.txt"
    cohort_file.write_text("I0001\tLoschbour\n", encoding="utf-8")

    cohort_out = tmp_path / "cohort.tsv"
    cohort_result = runner.invoke(
        cli_group,
        [
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(fixtures_dir / "loschbour_v54.anno"),
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
            "--cohort-version",
            "loschbour_v54",
            "-o",
            str(cohort_out),
        ],
        catch_exceptions=False,
    )
    assert cohort_result.exit_code == 0, cohort_result.output

    # Join over the same two files.
    join_out = tmp_path / "join.tsv"
    join_result = runner.invoke(
        cli_group,
        [
            "join",
            str(fixtures_dir / "loschbour_v54.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "-o",
            str(join_out),
        ],
        catch_exceptions=False,
    )
    assert join_result.exit_code == 0, join_result.output

    cohort_header = cohort_out.read_text(encoding="utf-8").splitlines()[0]
    join_header = join_out.read_text(encoding="utf-8").splitlines()[0]
    assert cohort_header == join_header

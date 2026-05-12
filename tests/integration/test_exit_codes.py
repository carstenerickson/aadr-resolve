"""Subprocess-based exit-code matrix.

Per LLD §5.1: exit-code tests use `subprocess.run`, not click's CliRunner,
because CliRunner doesn't route through cli.main()'s exit-code mapping.
These tests invoke `[sys.executable, "-m", "aadr_resolve", ...]` and
assert `result.returncode` against the HLD §Exit codes contract:

  0 = success
  1 = validation failure (turnover gate, coverage gate, etc.)
  2 = I/O failure (file not found, lock held)
  3 = invariant violation (schema detect fail, MID collision)
  4 = usage error (bad CLI args)"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from aadr_resolve.types import SchemaClass
from tests.fixtures.synthesize import SynthSpec, write_anno


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run `aadr-resolve <args>` and capture stdout + stderr."""
    return subprocess.run(
        [sys.executable, "-m", "aadr_resolve", *args],
        capture_output=True,
        text=True,
        check=False,
    )


# === Exit 0: success path ===


def test_exit_0_schema_subcommand_success(fixtures_dir: Path) -> None:
    """Smoke test the success path returns exit 0."""
    result = _run(["schema", str(fixtures_dir / "tiny_class_E.anno")])
    assert result.returncode == 0, f"stdout:{result.stdout}\nstderr:{result.stderr}"


# === Exit 1: validation failure ===


def test_exit_1_turnover_gate_failure(tmp_path: Path) -> None:
    """30% removal rate trips --turnover-fail (default 0.30) → exit 1."""
    v_old = tmp_path / "v_old.anno"
    v_new = tmp_path / "v_new.anno"
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=100, seed=11), v_old)
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=70, seed=11), v_new)

    result = _run(["--version-label", "v44.3", "diff", str(v_old), str(v_new)])
    assert result.returncode == 1
    assert "sample turnover gate (fail)" in result.stderr


# === Exit 2: I/O failure ===


def test_missing_anno_file_routes_to_failure(tmp_path: Path) -> None:
    """File-not-found on a positional .anno arg → click's `exists=True`
    validator emits exit 4 (usage error). For --mid-bridge we drop
    `exists=True` so the IOFailure path handles it as exit 2; positional
    .anno args keep click's pre-validation. Either is a clear failure
    signal — the test asserts non-zero exit + a descriptive stderr."""
    missing = tmp_path / "does_not_exist.anno"
    result = _run(["schema", str(missing)])
    assert result.returncode in (2, 4)
    assert "does not exist" in result.stderr or "not found" in result.stderr


def test_exit_2_missing_mid_bridge_file(fixtures_dir: Path, tmp_path: Path) -> None:
    """--mid-bridge PATH where PATH does not exist → exit 2."""
    missing = tmp_path / "does_not_exist.tsv"
    result = _run(
        [
            "--mid-bridge",
            str(missing),
            "lookup",
            "Loschbour",
            "--anno-files",
            str(fixtures_dir / "loschbour_v62.anno"),
        ]
    )
    # click validates `exists=True` on --mid-bridge before our handler runs.
    assert result.returncode == 2


# === Exit 3: invariant violation ===


def test_exit_3_schema_detection_failure(tmp_path: Path) -> None:
    """An .anno file with a header signature that doesn't match any known
    class → SchemaDetectionError → exit 3."""
    bad_anno = tmp_path / "bad.anno"
    # Header has an unknown col0/col1 pair and wrong ncols.
    bad_anno.write_text(
        "BogusCol0\tBogusCol1\tCol3\nval1\tval2\tval3\n",
        encoding="utf-8",
    )
    result = _run(["--version-label", "v44.3", "schema", str(bad_anno)])
    assert result.returncode == 3
    assert "unknown .anno schema signature" in result.stderr


def test_exit_3_mid_collision_with_default_policy(tmp_path: Path) -> None:
    """A cross-lab MID collision under default `--on-mid-collision error`
    → CollisionDetected → exit 3."""
    # Use the committed collision fixtures (HLD test 12 inputs).
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    collision_old = fixtures_dir / "collision_v_old.anno"
    collision_new = fixtures_dir / "collision_v_new.anno"
    if not (collision_old.exists() and collision_new.exists()):
        import pytest

        pytest.skip("collision fixtures not present; this test exercises CollisionDetected")

    result = _run(
        [
            "lookup",
            "MID-A",
            "--anno-files",
            str(collision_old),
            "--anno-files",
            str(collision_new),
        ]
    )
    assert result.returncode == 3


# === Exit 4: usage error ===


def test_exit_4_unknown_subcommand() -> None:
    """`aadr-resolve nonexistent-cmd` → click usage error → exit 4."""
    result = _run(["nonexistent-cmd"])
    assert result.returncode == 4


def test_exit_4_missing_required_arg() -> None:
    """`aadr-resolve schema` (no PATH arg) → click usage error → exit 4."""
    result = _run(["schema"])
    assert result.returncode == 4


def test_exit_4_invalid_schema_override() -> None:
    """`--schema-override Z` (not in A|B|C|D|E) → click usage error → exit 4."""
    result = _run(["--schema-override", "Z", "schema", "ignored.anno"])
    assert result.returncode == 4


def test_exit_4_cohort_no_version_match(tmp_path: Path) -> None:
    """`aadr-resolve cohort` where no supplied anno shares any IID with
    the cohort file → UsageError → exit 4 (per LLD §4.1 step 10)."""
    cohort_file = tmp_path / "cohort.txt"
    cohort_file.write_text("CompletelyUnknownIID\n", encoding="utf-8")
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    anno = fixtures_dir / "tiny_class_A.anno"

    result = _run(
        [
            "--version-label",
            "v44.3",
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(anno),
            "-o",
            str(tmp_path / "out.tsv"),
        ]
    )
    assert result.returncode == 4
    assert "could not auto-detect --cohort-version" in result.stderr

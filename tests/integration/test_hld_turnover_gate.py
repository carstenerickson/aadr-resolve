"""HLD tests 27 + 28: sample-turnover validation gate.

Gate (a) per HLD §Exit-1 validation gates: removal_rate >= --turnover-fail
exits 1; >= --turnover-warn warns to stderr without changing exit code.
Default warn=0.05, fail=0.30. Applies to both `diff` (single pair) and
`cohort` (max over consecutive version pairs).

Fixtures synthesized on the fly per test via tests.fixtures.synthesize:
two .anno files with overlapping IIDs (Synth0001 .. SynthNNNN) where the
smaller version drops the tail to produce a precise removal_rate."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge
from aadr_resolve.cli import cli as cli_group
from aadr_resolve.diff import compute_diff
from aadr_resolve.gates import (
    evaluate_turnover_cohort,
    evaluate_turnover_diff,
)
from aadr_resolve.types import SchemaClass
from tests.fixtures.synthesize import SynthSpec, write_anno


def _synth_pair(tmp_path: Path, n_old: int, n_new: int) -> tuple[Path, Path]:
    """Write two class-A .anno files. v_old has IIDs Synth0001..Synth{n_old},
    v_new has IIDs Synth0001..Synth{n_new}. removal_rate = (n_old-n_new) / n_old
    when n_new < n_old."""
    v_old_path = tmp_path / "v_old.anno"
    v_new_path = tmp_path / "v_new.anno"
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=n_old, seed=11), v_old_path)
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=n_new, seed=11), v_new_path)
    return v_old_path, v_new_path


# === HLD test 27: warns at 5%, exit 0 ===


def test_diff_turnover_gate_warns_at_5pct(tmp_path: Path) -> None:
    """Removal rate of exactly 5% triggers the warn threshold (default
    --turnover-warn=0.05); subcommand exits 0 with a stderr WARNING."""
    v_old, v_new = _synth_pair(tmp_path, n_old=100, n_new=95)

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v44.3",
            "diff",
            str(v_old),
            str(v_new),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "WARNING:" in result.stderr
    assert "sample turnover gate (warn)" in result.stderr
    assert "5.0%" in result.stderr


def test_diff_turnover_gate_eval_warn(tmp_path: Path) -> None:
    """Direct evaluate_turnover_diff: 5% rate → state='warn'."""
    v_old, v_new = _synth_pair(tmp_path, n_old=100, n_new=95)
    af_old = AnnoFrame.from_path(v_old, version_label="v_old")
    af_new = AnnoFrame.from_path(v_new, version_label="v_new")
    bridge = detect_bridge([af_old, af_new])
    diff_result = compute_diff(af_old, af_new, bridge=bridge)

    gate = evaluate_turnover_diff(diff_result, turnover_warn=0.05, turnover_fail=0.30)
    assert gate.state == "warn"
    assert abs(gate.removal_rate - 0.05) < 1e-9


# === HLD test 28: exits 1 at 30% ===


def test_diff_turnover_gate_exits_1_at_30pct(tmp_path: Path) -> None:
    """Removal rate of exactly 30% triggers --turnover-fail (default 0.30);
    subcommand raises ValidationError, which cli.main() routes to exit 1."""
    v_old, v_new = _synth_pair(tmp_path, n_old=100, n_new=70)

    from aadr_resolve.errors import ValidationError

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v44.3",
            "diff",
            str(v_old),
            str(v_new),
        ],
        catch_exceptions=True,
    )
    assert isinstance(result.exception, ValidationError)
    assert "sample turnover gate (fail)" in str(result.exception)
    assert "30.0%" in str(result.exception)


def test_diff_turnover_gate_eval_fail(tmp_path: Path) -> None:
    """Direct evaluate_turnover_diff: 30% rate → state='fail'."""
    v_old, v_new = _synth_pair(tmp_path, n_old=100, n_new=70)
    af_old = AnnoFrame.from_path(v_old, version_label="v_old")
    af_new = AnnoFrame.from_path(v_new, version_label="v_new")
    bridge = detect_bridge([af_old, af_new])
    diff_result = compute_diff(af_old, af_new, bridge=bridge)

    gate = evaluate_turnover_diff(diff_result, turnover_warn=0.05, turnover_fail=0.30)
    assert gate.state == "fail"
    assert abs(gate.removal_rate - 0.30) < 1e-9


# === Pass path: no warn, no fail ===


def test_diff_turnover_gate_below_warn_passes(tmp_path: Path) -> None:
    """1% removal rate is below warn=0.05; gate state='pass', no warning."""
    v_old, v_new = _synth_pair(tmp_path, n_old=100, n_new=99)

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v44.3",
            "diff",
            str(v_old),
            str(v_new),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "sample turnover gate" not in result.stderr


# === Threshold-override tests ===


def test_diff_turnover_gate_custom_thresholds(tmp_path: Path) -> None:
    """Custom --turnover-warn / --turnover-fail are honored. A 5% rate
    is the pass state when warn=0.10."""
    v_old, v_new = _synth_pair(tmp_path, n_old=100, n_new=95)

    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "--version-label",
            "v44.3",
            "diff",
            str(v_old),
            str(v_new),
            "--turnover-warn",
            "0.10",
            "--turnover-fail",
            "0.50",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "sample turnover gate" not in result.stderr


# === Cohort: per-pair evaluation ===


def test_cohort_turnover_gate_per_pair_eval(tmp_path: Path) -> None:
    """For a 3-version cohort with one warning pair and one passing pair,
    evaluate_turnover_cohort returns a result per consecutive pair."""
    v44 = tmp_path / "v44.anno"
    v50 = tmp_path / "v50.anno"
    v52 = tmp_path / "v52.anno"
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=100, seed=11), v44)
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=95, seed=11), v50)
    write_anno(SynthSpec(schema_class=SchemaClass.B, n_samples=94, seed=11), v52)

    af_v44 = AnnoFrame.from_path(v44, version_label="v44.3")
    af_v50 = AnnoFrame.from_path(v50, version_label="v50.0")
    af_v52 = AnnoFrame.from_path(v52, version_label="v52.2")
    bridge = detect_bridge([af_v44, af_v50, af_v52])

    from aadr_resolve.cohort import build_manifest
    from aadr_resolve.library_token import build_all_library_identities

    cohort_input = {f"Synth{i + 1:04d}": None for i in range(100)}
    library_identities = build_all_library_identities([af_v44, af_v50, af_v52], bridge)
    manifest = build_manifest(
        cohort_input,
        [af_v44, af_v50, af_v52],
        bridge,
        library_identities,
        cohort_version="v44.3",
    )

    gates = evaluate_turnover_cohort(manifest, turnover_warn=0.05, turnover_fail=0.30)
    assert len(gates) == 2
    # v44 -> v50: 5% removal → warn.
    assert gates[0].v_old == "v44.3"
    assert gates[0].v_new == "v50.0"
    assert gates[0].state == "warn"
    # v50 -> v52: 1/95 ≈ 1.05% → pass.
    assert gates[1].v_old == "v50.0"
    assert gates[1].v_new == "v52.2"
    assert gates[1].state == "pass"


def test_cohort_turnover_gate_empty_versions_returns_empty(tmp_path: Path) -> None:
    """Single-version cohort has no consecutive pairs → empty gate list."""
    v44 = tmp_path / "v44.anno"
    write_anno(SynthSpec(schema_class=SchemaClass.A, n_samples=10, seed=11), v44)
    af = AnnoFrame.from_path(v44, version_label="v44.3")
    bridge = detect_bridge([af])

    from aadr_resolve.cohort import build_manifest
    from aadr_resolve.library_token import build_all_library_identities

    cohort_input = {f"Synth{i + 1:04d}": None for i in range(10)}
    library_identities = build_all_library_identities([af], bridge)
    manifest = build_manifest(
        cohort_input,
        [af],
        bridge,
        library_identities,
        cohort_version="v44.3",
    )

    gates = evaluate_turnover_cohort(manifest, turnover_warn=0.05, turnover_fail=0.30)
    assert gates == []

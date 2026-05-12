"""`aadr-resolve cohort` subcommand. Per LLD §4.1."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from ..cohort import (
    build_cohort_run_summary,
    build_manifest,
    detect_cohort_version,
    parse_cohort_file,
)
from ..errors import UsageError, ValidationError
from ..gates import (
    evaluate_cohort_coverage_gate,
    evaluate_turnover_cohort,
    format_cohort_coverage_message,
    format_gate_message,
)
from ..library_token import build_all_library_identities
from ..reporting import format_stdout_summary, write_cohort_json, write_cohort_tsv
from ..types import SchemaClass


@click.command()
@click.argument("cohort_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--anno-files",
    "anno_paths",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="One or more .anno files. Repeat the flag for each file.",
)
@click.option(
    "--cohort-version",
    type=str,
    default=None,
    help="Version label whose IIDs the cohort file uses (default: auto-detect).",
)
@click.option(
    "-o",
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output TSV (or JSON when --json is set).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON array of rows.")
@click.option(
    "--no-propagate",
    is_flag=True,
    help="Disable cohort_label propagation across versions.",
)
@click.option(
    "--collapse-to-individual",
    "collapse",
    is_flag=True,
    help="One row per individual instead of one per library.",
)
@click.option(
    "--gid-preference",
    type=str,
    default="AG,DG,SG,HO,TW,BY,AA,EC,WGC,bare",
    help="Suffix priority for --collapse-to-individual (comma-separated).",
)
@click.option(
    "--turnover-warn",
    type=float,
    default=0.05,
    show_default=True,
    help="Sample-removal-rate warn threshold (per consecutive version pair).",
)
@click.option(
    "--turnover-fail",
    type=float,
    default=0.30,
    show_default=True,
    help="Sample-removal-rate fail threshold; exit 1 if any pair exceeds.",
)
@click.option(
    "--cohort-coverage-warn",
    type=float,
    default=0.50,
    show_default=True,
    help="Stderr WARNING when resolved cohort fraction drops below this.",
)
@click.option(
    "--cohort-coverage-fail",
    type=float,
    default=0.25,
    show_default=True,
    help="Exit 1 when resolved cohort fraction drops below this.",
)
@click.pass_context
def cohort_cmd(  # noqa: PLR0912,PLR0915 (orchestrator: linear setup + 2 gates × {warn,fail} + summary)
    ctx: click.Context,
    cohort_file: Path,
    anno_paths: tuple[Path, ...],
    cohort_version: str | None,
    out_path: Path,
    as_json: bool,
    no_propagate: bool,
    collapse: bool,
    gid_preference: str,
    turnover_warn: float,
    turnover_fail: float,
    cohort_coverage_warn: float,
    cohort_coverage_fail: float,
) -> None:
    """Emit a cross-version cohort manifest."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}
    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")
    mid_bridge_path = shared.get("mid_bridge_path")
    on_mid_collision = shared.get("on_mid_collision", "error")
    quiet = bool(shared.get("quiet", False))

    t_start = time.perf_counter()

    anno_frames = [
        AnnoFrame.from_path(p, version_label=version_label, schema_override=schema_override)
        for p in anno_paths
    ]

    bridge = detect_bridge(anno_frames, on_collision=on_mid_collision)
    bridge_manual_count = 0
    if mid_bridge_path is not None:
        overrides = load_manual_bridge(mid_bridge_path)
        bridge_manual_count = len(overrides)
        bridge, warnings = merge_with_overrides(bridge, overrides)
        for w in warnings:
            sys.stderr.write(f"WARNING: {w}\n")

    cohort_input = parse_cohort_file(cohort_file)

    if cohort_version is None:
        detected = detect_cohort_version(set(cohort_input), anno_frames, bridge)
        if detected is None:
            raise UsageError(
                "could not auto-detect --cohort-version: no supplied .anno "
                "shares any individual_id with the cohort file. Use "
                "--cohort-version VERSION to specify explicitly."
            )
        cohort_version = detected

    library_identities = build_all_library_identities(anno_frames, bridge)

    preference = tuple(p.strip() for p in gid_preference.split(",") if p.strip())

    manifest = build_manifest(
        cohort_input,
        anno_frames,
        bridge,
        library_identities,
        cohort_version=cohort_version,
        no_propagate=no_propagate,
        collapse=collapse,
        gid_preference=preference,
    )

    if as_json:
        write_cohort_json(manifest, out_path)
        # JSON output: 'columns' isn't meaningful; report fields-per-row.
        n_cols_written = 9
    else:
        write_cohort_tsv(manifest, out_path)
        # Count actual TSV columns from the header line.
        header_line = out_path.read_text(encoding="utf-8").splitlines()[0]
        n_cols_written = len(header_line.split("\t"))

    for w in manifest.warnings:
        sys.stderr.write(f"WARNING: {w}\n")

    gates = evaluate_turnover_cohort(
        manifest, turnover_warn=turnover_warn, turnover_fail=turnover_fail
    )
    failed: list[str] = []
    for gate in gates:
        gate_msg = format_gate_message(gate, warn_pct=turnover_warn, fail_pct=turnover_fail)
        if gate.state == "warn":
            sys.stderr.write(f"WARNING: {gate_msg}\n")
        elif gate.state == "fail":
            failed.append(gate_msg)

    coverage_gate = evaluate_cohort_coverage_gate(
        cohort_input,
        manifest,
        bridge=bridge,
        cohort_version=cohort_version,
        coverage_warn=cohort_coverage_warn,
        coverage_fail=cohort_coverage_fail,
    )
    coverage_msg = format_cohort_coverage_message(
        coverage_gate,
        warn_pct=cohort_coverage_warn,
        fail_pct=cohort_coverage_fail,
    )
    if coverage_gate.state == "warn":
        sys.stderr.write(f"WARNING: {coverage_msg}\n")
    elif coverage_gate.state == "fail":
        failed.append(coverage_msg)

    elapsed = time.perf_counter() - t_start

    if not quiet:
        summary = build_cohort_run_summary(
            manifest=manifest,
            anno_frames=anno_frames,
            bridge=bridge,
            bridge_manual_count=bridge_manual_count,
            cohort_input_path=cohort_file,
            cohort_input_n_individuals=len(cohort_input),
            out_path=out_path,
            n_cols_written=n_cols_written,
            turnover_gates=gates,
            elapsed_seconds=elapsed,
        )
        sys.stdout.write(format_stdout_summary(summary))

    if failed:
        raise ValidationError("; ".join(failed))

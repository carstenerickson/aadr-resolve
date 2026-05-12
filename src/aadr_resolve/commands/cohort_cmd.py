"""`aadr-resolve cohort` subcommand. Per LLD §4.1."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from ..cohort import build_manifest, detect_cohort_version, parse_cohort_file
from ..errors import UsageError, ValidationError
from ..gates import evaluate_turnover_cohort, format_gate_message
from ..library_token import build_all_library_identities
from ..reporting import write_cohort_json, write_cohort_tsv
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
@click.pass_context
def cohort_cmd(
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
) -> None:
    """Emit a cross-version cohort manifest."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}
    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")
    mid_bridge_path = shared.get("mid_bridge_path")
    on_mid_collision = shared.get("on_mid_collision", "error")
    quiet = bool(shared.get("quiet", False))

    anno_frames = [
        AnnoFrame.from_path(p, version_label=version_label, schema_override=schema_override)
        for p in anno_paths
    ]

    bridge = detect_bridge(anno_frames, on_collision=on_mid_collision)
    if mid_bridge_path is not None:
        overrides = load_manual_bridge(mid_bridge_path)
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
    else:
        write_cohort_tsv(manifest, out_path)

    if not quiet:
        sys.stdout.write(
            f"Wrote {manifest.n_libraries} rows "
            f"({manifest.n_individuals} individuals) to {out_path}\n"
        )

    for w in manifest.warnings:
        sys.stderr.write(f"WARNING: {w}\n")

    gates = evaluate_turnover_cohort(
        manifest, turnover_warn=turnover_warn, turnover_fail=turnover_fail
    )
    failed: list[str] = []
    for gate in gates:
        if gate.state == "warn":
            sys.stderr.write(
                f"WARNING: "
                f"{format_gate_message(gate, warn_pct=turnover_warn, fail_pct=turnover_fail)}\n"
            )
        elif gate.state == "fail":
            failed.append(format_gate_message(gate, warn_pct=turnover_warn, fail_pct=turnover_fail))
    if failed:
        raise ValidationError("; ".join(failed))

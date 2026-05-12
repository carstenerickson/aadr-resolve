"""`aadr-resolve join` subcommand. Per LLD §4.4."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from ..join import compute_join
from ..reporting import write_cohort_json, write_cohort_tsv
from ..types import SchemaClass


@click.command()
@click.argument("v_old_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("v_new_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o",
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output TSV path (or JSON when --json is set).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON array of rows.")
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
@click.pass_context
def join_cmd(
    ctx: click.Context,
    v_old_path: Path,
    v_new_path: Path,
    out_path: Path,
    as_json: bool,
    collapse: bool,
    gid_preference: str,
) -> None:
    """Wide-format pairwise cross-version table."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}
    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")
    mid_bridge_path = shared.get("mid_bridge_path")
    on_mid_collision = shared.get("on_mid_collision", "error")
    quiet = bool(shared.get("quiet", False))

    af_old = AnnoFrame.from_path(
        v_old_path, version_label=version_label, schema_override=schema_override
    )
    af_new = AnnoFrame.from_path(
        v_new_path, version_label=version_label, schema_override=schema_override
    )

    bridge = detect_bridge([af_old, af_new], on_collision=on_mid_collision)
    if mid_bridge_path is not None:
        overrides = load_manual_bridge(mid_bridge_path)
        bridge, warnings = merge_with_overrides(bridge, overrides)
        for w in warnings:
            sys.stderr.write(f"WARNING: {w}\n")

    preference = tuple(p.strip() for p in gid_preference.split(",") if p.strip())

    manifest = compute_join(
        af_old,
        af_new,
        bridge,
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

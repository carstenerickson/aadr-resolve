"""`aadr-resolve lookup` subcommand. Per LLD §4.3."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from ..lookup import lookup_single
from ..types import LookupResult, SchemaClass


@click.command()
@click.argument("query")
@click.option(
    "--anno-files",
    "anno_paths",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="One or more .anno files to search across.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of plain text.")
@click.pass_context
def lookup_cmd(ctx: click.Context, query: str, anno_paths: tuple[Path, ...], as_json: bool) -> None:
    """Resolve a single individual_id or genetic_id across AADR versions."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}
    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")
    mid_bridge_path = shared.get("mid_bridge_path")
    on_mid_collision = shared.get("on_mid_collision", "error")

    anno_frames = [
        AnnoFrame.from_path(
            p,
            version_label=version_label,
            schema_override=schema_override,
        )
        for p in anno_paths
    ]

    bridge = detect_bridge(anno_frames, on_collision=on_mid_collision)
    if mid_bridge_path is not None:
        overrides = load_manual_bridge(mid_bridge_path)
        bridge, warnings = merge_with_overrides(bridge, overrides)
        for w in warnings:
            sys.stderr.write(f"WARNING: {w}\n")

    result = lookup_single(query, anno_frames, bridge=bridge)

    if as_json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    sys.stdout.write(_format_lookup(result))


def _format_lookup(result: LookupResult) -> str:
    """Human-readable stdout block per HLD §Output: lookup."""
    lines: list[str] = []
    lines.append(f"query:                       {result.query}")
    lines.append(f"individual_id (canonical):   {result.individual_id_canonical}")
    lines.append(f"matched_via:                 {result.matched_via}")

    if result.matched_via == "not_found":
        lines.append("")
        lines.append(f"  query {result.query!r} did not match any individual_id or genetic_id in")
        lines.append("  the supplied .anno files.")
        return "\n".join(lines) + "\n"

    # Day-4: master_id_bridge will populate here.
    if result.master_id_bridge:
        lines.append("master_id_bridge:")
        for event in result.master_id_bridge:
            lines.append(
                f"  {event['v_old_label']}={event['mid_old']!r} -> "
                f"{event['v_new_label']}={event['mid_new']!r} "
                f"[via {event.get('via_genetic_id', '?')!r}]"
            )

    # Per-version rows, in user-supplied order.
    for version_label, rows in result.per_version.items():
        for row in rows:
            details = [f"genetic_id={row.genetic_id}", f"group_id={row.group_id}"]
            if row.snps_hit_1240k is not None:
                details.append(f"snps_hit_1240k={row.snps_hit_1240k}")
            if row.persistent_genetic_id is not None:
                details.append(f"persistent_genetic_id={row.persistent_genetic_id}")
            lines.append(f"{version_label}:".ljust(28) + " ".join(details))

    lines.append("")
    lines.append("status: " + "; ".join(result.status_flags))
    return "\n".join(lines) + "\n"

"""`aadr-resolve diff` subcommand. Per LLD §4.2."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from ..diff import compute_diff
from ..types import DiffResult, SchemaClass


@click.command()
@click.argument("v_old_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("v_new_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, default=True, help="JSON output (default).")
@click.option("--tsv", "as_tsv", is_flag=True, default=False, help="One row per change event.")
@click.option(
    "-o",
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write to file instead of stdout.",
)
@click.pass_context
def diff_cmd(
    ctx: click.Context,
    v_old_path: Path,
    v_new_path: Path,
    as_json: bool,
    as_tsv: bool,
    out_path: Path | None,
) -> None:
    """Structured diff between two .anno files."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}
    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")
    mid_bridge_path = shared.get("mid_bridge_path")
    on_mid_collision = shared.get("on_mid_collision", "error")

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

    result = compute_diff(af_old, af_new, bridge=bridge)

    text = (
        _format_diff_tsv(result)
        if as_tsv
        # --json is the default; explicit --json doesn't change behavior.
        else json.dumps(result.to_dict(), indent=2) + "\n"
    )

    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _format_diff_tsv(result: DiffResult) -> str:
    """One row per change event (per HLD §Output: diff TSV mode)."""
    lines: list[str] = ["event_class\tindividual_id\tdetails"]
    for events in (
        result.added,
        result.removed,
        result.genetic_id_renamed,
        result.master_id_renamed,
    ):
        for e in events:
            lines.append(f"{e.event_class}\t{e.individual_id_canonical}\t{json.dumps(e.details)}")
    for cls, events in result.group_changed_by_class.items():
        for e in events:
            lines.append(
                f"group_changed:{cls.value}\t{e.individual_id_canonical}\t{json.dumps(e.details)}"
            )
    return "\n".join(lines) + "\n"

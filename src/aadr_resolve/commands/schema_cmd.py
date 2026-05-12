"""`aadr-resolve schema` subcommand. Per LLD §4.5."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..types import SchemaClass


@click.command()
@click.argument("anno_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of plain text.")
@click.pass_context
def schema_cmd(ctx: click.Context, anno_path: Path, as_json: bool) -> None:
    """Report the detected schema class for an .anno file."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}

    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")

    af = AnnoFrame.from_path(
        anno_path,
        version_label=version_label,
        schema_override=schema_override,
    )

    if as_json:
        json.dump(af.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    sys.stdout.write(_format_summary(af))


def _format_summary(af: AnnoFrame) -> str:
    """Human-readable summary block per HLD §Output: schema."""
    lines: list[str] = []
    lines.append(f"path:                {af.df.attrs.get('source_path', '(stdin)')}")
    lines.append(f"version (inferred):  {af.version}")
    lines.append(f"schema_class:        {af.schema_class.value}")
    lines.append(f"n_rows:              {af.n_rows:,}")
    lines.append(f"n_columns:           {af.n_columns}")
    sig = af.schema_def.detection_signature
    lines.append(f"detection_signature: (ncols={af.n_columns}, col0={sig[0]!r}, col1={sig[1]!r})")
    lines.append("")
    lines.append("mapped canonical fields:")
    for canonical, mapping in sorted(af.schema_def.fields.items(), key=lambda kv: kv[1].column):
        display = mapping.display_header or mapping.normalized_header
        lines.append(f"  col {mapping.column:2d}  {canonical:<32}  {display}")
    if af.schema_def.not_present:
        lines.append("")
        lines.append("fields NOT present in this class:")
        for canonical in af.schema_def.not_present:
            lines.append(f"  - {canonical}")
    if af.schema_def.notes:
        lines.append("")
        lines.append("notes:")
        for note in af.schema_def.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines) + "\n"

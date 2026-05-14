"""`aadr-resolve validate-groups` subcommand. Per LLD §4.6."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..errors import ValidationError
from ..types import SchemaClass
from ..validate_groups import GroupValidationItem, validate_groups as _validate_groups


@click.command()
@click.argument("group_ids", nargs=-1, required=True)
@click.option(
    "--anno-files",
    "anno_paths",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="One or more .anno files to validate against.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON result instead of plain text.")
@click.pass_context
def validate_groups_cmd(
    ctx: click.Context,
    group_ids: tuple[str, ...],
    anno_paths: tuple[Path, ...],
    as_json: bool,
) -> None:
    """Validate group IDs against the supplied AADR panel versions.

    Each GROUP_ID is checked against the group_id column of every supplied
    .anno file.  Known v44-era prefix drops (e.g. Patterson_England_IA →
    England_IA) are suggested automatically.

    Exits non-zero when any group ID is absent from the panel and has no
    known lift."""
    shared = ctx.obj["shared_opts"] if ctx.obj else {}
    schema_override_raw = shared.get("schema_override")
    schema_override = SchemaClass(schema_override_raw) if schema_override_raw else None
    version_label = shared.get("version_label")

    anno_frames = [
        AnnoFrame.from_path(p, version_label=version_label, schema_override=schema_override)
        for p in anno_paths
    ]

    result = _validate_groups(list(group_ids), anno_frames)

    if as_json:
        json.dump(
            [_item_to_dict(i) for i in result.items],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        for item in result.items:
            if item.status == "lifted":
                sys.stderr.write(
                    f"WARNING: group ID '{item.group_id}' not found in panel.\n"
                    f"         Suggested lift: '{item.lifted_to}'"
                    f" (convention_restructure_prefix_drop);"
                    f" found in: {', '.join(item.found_in)}\n"
                )
            elif item.status == "unresolvable":
                sys.stderr.write(
                    f"WARNING: group ID '{item.group_id}' not found in any supplied"
                    f" panel and no known lift exists.\n"
                )

    if result.has_failures:
        raise ValidationError(
            f"{len(result.unresolvable)} group ID(s) unresolvable: "
            + ", ".join(f"'{i.group_id}'" for i in result.unresolvable)
        )


def _item_to_dict(item: GroupValidationItem) -> dict[str, object]:
    return {
        "group_id": item.group_id,
        "status": item.status,
        "lifted_to": item.lifted_to,
        "found_in": list(item.found_in),
    }

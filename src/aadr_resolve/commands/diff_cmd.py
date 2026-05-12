"""`aadr-resolve diff` subcommand. Per LLD §4.2."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..annoframe import AnnoFrame
from ..bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from ..diff import compute_diff
from ..errors import ValidationError
from ..gates import (
    evaluate_substantive_regroup_gate,
    evaluate_turnover_diff,
    format_gate_message,
    format_substantive_regroup_message,
)
from ..types import DiffResult, GroupChangeClass, SchemaClass

# Stderr-warn threshold for buffered JSON size when --all-events is set.
SIZE_WARN_THRESHOLD_BYTES: int = 100 * 1024 * 1024  # 100 MB


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
@click.option(
    "--include-class",
    "include_classes",
    multiple=True,
    type=click.Choice([c.value for c in GroupChangeClass]),
    help="Include the per-event array for a Group-ID-change class. Repeatable.",
)
@click.option(
    "--all-events",
    is_flag=True,
    help=(
        "Include per-event arrays for ALL classes (plus added/removed/"
        "genetic_id_renamed events). Warns to stderr if predicted JSON "
        "size > 100 MB; prefer --tsv at scale."
    ),
)
@click.option(
    "--turnover-warn",
    type=float,
    default=0.05,
    show_default=True,
    help="Sample-removal-rate warn threshold; stderr warning at or above.",
)
@click.option(
    "--turnover-fail",
    type=float,
    default=0.30,
    show_default=True,
    help="Sample-removal-rate fail threshold; exit 1 at or above.",
)
@click.option(
    "--substantive-regroup-fail",
    type=int,
    default=None,
    help=(
        "Exit 1 if substantive_regroup group_id-change count exceeds this. "
        "Default: gate disabled (opt-in for strict CI)."
    ),
)
@click.pass_context
def diff_cmd(
    ctx: click.Context,
    v_old_path: Path,
    v_new_path: Path,
    as_json: bool,
    as_tsv: bool,
    out_path: Path | None,
    include_classes: tuple[str, ...],
    all_events: bool,
    turnover_warn: float,
    turnover_fail: float,
    substantive_regroup_fail: int | None,
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

    include_class_set: set[GroupChangeClass] = {GroupChangeClass(s) for s in include_classes}

    if as_tsv:
        text = _format_diff_tsv(result)
    else:
        if all_events:
            predicted = result.predict_json_size_bytes(
                include_class=include_class_set, all_events=all_events
            )
            if predicted > SIZE_WARN_THRESHOLD_BYTES:
                sys.stderr.write(
                    f"WARNING: predicted JSON size {predicted / (1024 * 1024):.1f} MB "
                    f"exceeds {SIZE_WARN_THRESHOLD_BYTES / (1024 * 1024):.0f} MB; "
                    f"prefer --tsv for large diffs.\n"
                )
        text = (
            json.dumps(
                result.to_dict(include_class=include_class_set, all_events=all_events),
                indent=2,
            )
            + "\n"
        )

    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    gate = evaluate_turnover_diff(result, turnover_warn=turnover_warn, turnover_fail=turnover_fail)
    msg = format_gate_message(gate, warn_pct=turnover_warn, fail_pct=turnover_fail)
    failed: list[str] = []
    if gate.state == "warn":
        sys.stderr.write(f"WARNING: {msg}\n")
    elif gate.state == "fail":
        failed.append(msg)

    regroup_gate = evaluate_substantive_regroup_gate(
        result, fail_threshold=substantive_regroup_fail
    )
    if regroup_gate.state == "fail":
        failed.append(format_substantive_regroup_message(regroup_gate))

    if failed:
        raise ValidationError("; ".join(failed))


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

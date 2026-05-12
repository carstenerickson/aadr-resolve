"""Cohort manifest writers + stdout summary renderer. Per LLD §3.14."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import CohortManifest, CohortRunSummary, DiffRunSummary

# Missing-cell sentinel per HLD §Output: cohort manifest TSV.
TSV_NULL_SENTINEL = "--"


def write_cohort_tsv(manifest: CohortManifest, path: Path) -> None:
    """Write the cohort manifest as TSV (HLD §Output: cohort).

    Stable column order:
      cohort_label, cohort_label_source, individual_id_canonical,
      library_token,
      then per-version columns in user-supplied order:
        v{X}_genetic_id, v{X}_group_id, v{X}_snps_hit_1240k,
      then per-adjacent-pair columns:
        group_id_change_class_v_{old}_to_v_{new}
        (one per consecutive pair; LLD §4.1 step 11d),
      then v{LATEST}_persistent_genetic_id (when latest is class E),
      then status.

    Missing-cell sentinel: '--' for ALL columns (string + Int64 nulls)."""
    versions = manifest.versions_supplied
    columns: list[str] = [
        "cohort_label",
        "cohort_label_source",
        "individual_id_canonical",
        "library_token",
    ]
    for v in versions:
        prefix = _column_prefix(v)
        columns.append(f"{prefix}_genetic_id")
        columns.append(f"{prefix}_group_id")
        columns.append(f"{prefix}_snps_hit_1240k")
    pair_keys: list[tuple[str, str]] = []
    for i in range(len(versions) - 1):
        v_old, v_new = versions[i], versions[i + 1]
        pair_keys.append((v_old, v_new))
        columns.append(f"group_id_change_class_{_column_prefix(v_old)}_to_{_column_prefix(v_new)}")
    # PGID only emitted if at least one row has one populated.
    has_pgid = any(r.persistent_genetic_id is not None for r in manifest.rows)
    if has_pgid:
        columns.append("persistent_genetic_id")
    columns.append("status")

    lines = ["\t".join(columns)]
    for row in manifest.rows:
        cells: list[str] = [
            row.cohort_label,
            row.cohort_label_source,
            row.individual_id_canonical,
            row.library_token,
        ]
        for v in versions:
            cells.append(_cell(row.per_version_gid.get(v)))
            cells.append(_cell(row.per_version_group_id.get(v)))
            cells.append(_cell(row.per_version_snps_hit_1240k.get(v)))
        for pair in pair_keys:
            cells.append(_cell(row.per_pair_group_change_class.get(pair)))
        if has_pgid:
            cells.append(_cell(row.persistent_genetic_id))
        cells.append(row.status)
        lines.append("\t".join(cells))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_cohort_json(manifest: CohortManifest, path: Path) -> None:
    """Write the cohort manifest as JSON array of row-objects.

    Missing cells become JSON null (not '--' — HLD-pinned asymmetry: TSV
    optimizes for human readability; JSON for tool consumption)."""
    payload: list[dict[str, Any]] = []
    for row in manifest.rows:
        # JSON keys can't be tuples; render per-pair keys as
        # "{v_old}__to__{v_new}" strings (double-underscore separator).
        per_pair_str: dict[str, str | None] = {
            f"{v_old}__to__{v_new}": cls
            for (v_old, v_new), cls in row.per_pair_group_change_class.items()
        }
        payload.append(
            {
                "cohort_label": row.cohort_label,
                "cohort_label_source": row.cohort_label_source,
                "individual_id_canonical": row.individual_id_canonical,
                "library_token": row.library_token,
                "per_version_gid": row.per_version_gid,
                "per_version_group_id": row.per_version_group_id,
                "per_version_snps_hit_1240k": row.per_version_snps_hit_1240k,
                "per_pair_group_change_class": per_pair_str,
                "persistent_genetic_id": row.persistent_genetic_id,
                "status": row.status,
            }
        )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_report_json_summary(summary: CohortRunSummary | DiffRunSummary, path: Path) -> None:
    """Write the v0.2 A2 `--report-json` sidecar.

    JSON shape per HLD §Reports + LLD §3.14: ~few KB regardless of
    corpus size, loadable cheaply via `json.load`. Sibling tools
    (ancestry-pipeline-tool, CI dashboards) consume this for run-level
    status. Cohort variant has a `cohort` block (resolution + histograms);
    diff variant has a `diff` block (event counts + group-change-by-class).
    `gates`, `warnings`, `config` are common to both."""
    payload = _serialize_summary(summary)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _serialize_summary(summary: CohortRunSummary | DiffRunSummary) -> dict[str, Any]:
    """Build the JSON-serializable dict for the report-json sidecar."""
    schemas_detected = {
        info.version_label: info.schema_class.value for info in summary.anno_file_info
    }
    bridge_block: dict[str, Any] = {
        "auto_count": summary.bridge_auto_count,
        "manual_count": summary.bridge_manual_count,
        "collisions": list(summary.bridge_collisions),
    }

    if isinstance(summary, CohortRunSummary):
        cohort_or_diff_key = "cohort"
        cohort_or_diff_block: dict[str, Any] = {
            "n_individuals": summary.n_individuals,
            "n_libraries": summary.n_rows_written,
            "n_resolved_in_latest": summary.n_resolved_in_latest,
            "n_added_after_earliest": summary.n_added_after_earliest,
            "n_removed_before_latest": summary.n_removed_before_latest,
            "label_source_histogram": dict(summary.label_source_histogram),
            "status_histogram": dict(summary.status_histogram),
            "group_change_by_class": dict(summary.group_change_by_class),
        }
        gates_block = {
            "turnover": summary.turnover_state,
            "turnover_rate": summary.turnover_rate,
            "cohort_coverage": summary.cohort_coverage_state,
            "cohort_coverage_rate": summary.cohort_coverage_rate,
        }
    else:
        cohort_or_diff_key = "diff"
        cohort_or_diff_block = {
            "added": summary.n_added,
            "removed": summary.n_removed,
            "genetic_id_renamed": summary.n_genetic_id_renamed,
            "master_id_renamed": summary.n_master_id_renamed,
            "group_change_by_class": dict(summary.group_change_by_class),
        }
        gates_block = {
            "turnover": summary.turnover_state,
            "turnover_rate": summary.turnover_rate,
            "substantive_regroup": summary.substantive_regroup_state,
            "substantive_regroup_count": summary.substantive_regroup_count,
        }

    return {
        "versions_supplied": list(summary.versions_supplied),
        "schemas_detected": schemas_detected,
        "bridge": bridge_block,
        cohort_or_diff_key: cohort_or_diff_block,
        "gates": gates_block,
        "warnings": list(summary.warnings),
        "config": dict(summary.config),
        "elapsed_seconds": summary.elapsed_seconds,
    }


def format_stdout_summary(summary: CohortRunSummary | DiffRunSummary) -> str:
    """Render the stdout summary block per HLD §Stdout summary block.

    Dispatches on the summary type; cohort and diff blocks share the
    anno-file header, bridge, group-change histogram, and timing
    sections. Cohort adds a cohort-input resolution histogram; diff adds
    a change-event histogram. Multi-line; caller writes via stdout unless
    `--quiet` (diff routes the block to stderr when output is going to
    stdout — see diff_cmd)."""
    if isinstance(summary, CohortRunSummary):
        return _format_cohort_summary(summary)
    return _format_diff_summary(summary)


def _format_anno_block(
    anno_file_info: tuple[Any, ...],
) -> list[str]:
    """Shared 'Loaded N .anno files' header lines."""
    lines = [f"Loaded {len(anno_file_info)} .anno file(s):"]
    for info in anno_file_info:
        lines.append(
            f"  [{info.version_label}] {info.path.name}: "
            f"{info.n_rows:,} rows × {info.n_cols} cols, class {info.schema_class.value}"
        )
    return lines


def _format_bridge_block(
    bridge_auto_count: int,
    bridge_manual_count: int,
    bridge_collisions: tuple[str, ...],
) -> list[str]:
    """Shared 'Cross-version bridge' lines."""
    collision_msg = (
        f"{len(bridge_collisions)} collision(s)" if bridge_collisions else "no collisions detected"
    )
    return [
        "Cross-version bridge:",
        f"  GID-stable MID-rename detection:  {bridge_auto_count} events",
        f"  Manual --mid-bridge entries:      {bridge_manual_count}",
        f"  Cross-lab MID collision check:    {collision_msg}",
    ]


def _format_group_change_histogram(
    group_change_by_class: dict[str, int],
    versions_supplied: tuple[str, ...],
) -> list[str]:
    """Shared 'Group ID changes' histogram, empty list when no class has events."""
    if not any(group_change_by_class.values()):
        return []
    first_v = versions_supplied[0] if versions_supplied else ""
    last_v = versions_supplied[-1] if versions_supplied else ""
    lines = [f"Group ID changes ({first_v} → {last_v}):"]
    for cls in (
        "convention_restructure_suffix",
        "convention_restructure_country",
        "convention_restructure_order",
        "convention_restructure_punct",
        "partial",
        "substantive_regroup",
    ):
        count = group_change_by_class.get(cls, 0)
        if count > 0:
            lines.append(f"  {cls:34s}  {count}")
    return lines


def _format_cohort_summary(summary: CohortRunSummary) -> str:
    lines: list[str] = []
    lines.extend(_format_anno_block(summary.anno_file_info))
    lines.append("")
    lines.extend(
        _format_bridge_block(
            summary.bridge_auto_count, summary.bridge_manual_count, summary.bridge_collisions
        )
    )

    lines.append("")
    cohort_path_label = summary.cohort_input_path.name if summary.cohort_input_path else "<stdin>"
    lines.append(
        f"Cohort input: {cohort_path_label} ({summary.cohort_input_n_individuals} individuals)"
    )
    lines.append(f"  Resolved in latest version:  {summary.n_resolved_in_latest}")
    lines.append(f"  Added after earliest:        {summary.n_added_after_earliest}")
    lines.append(f"  Removed before latest:       {summary.n_removed_before_latest}")

    group_block = _format_group_change_histogram(
        summary.group_change_by_class, summary.versions_supplied
    )
    if group_block:
        lines.append("")
        lines.extend(group_block)

    lines.append("")
    lines.append(
        f"Wrote {summary.out_path.name} "
        f"({summary.n_rows_written} rows × {summary.n_cols_written} cols)"
    )
    if summary.turnover_state != "n/a":
        verdict = summary.turnover_state.upper()
        lines.append(
            f"Sample turnover within cohort: {100 * summary.turnover_rate:.1f}% — {verdict}"
        )

    lines.append("")
    lines.append(f"Done in {summary.elapsed_seconds:.1f}s.")
    return "\n".join(lines) + "\n"


def _format_diff_summary(summary: DiffRunSummary) -> str:
    lines: list[str] = []
    lines.extend(_format_anno_block(summary.anno_file_info))
    lines.append("")
    lines.extend(
        _format_bridge_block(
            summary.bridge_auto_count, summary.bridge_manual_count, summary.bridge_collisions
        )
    )

    # Diff event histogram replaces the cohort-input section.
    lines.append("")
    lines.append("Diff events:")
    lines.append(f"  added:                {summary.n_added}")
    lines.append(f"  removed:              {summary.n_removed}")
    lines.append(f"  genetic_id_renamed:   {summary.n_genetic_id_renamed}")
    lines.append(f"  master_id_renamed:    {summary.n_master_id_renamed}")

    group_block = _format_group_change_histogram(
        summary.group_change_by_class, summary.versions_supplied
    )
    if group_block:
        lines.append("")
        lines.extend(group_block)

    lines.append("")
    if summary.out_path is not None:
        lines.append(f"Wrote {summary.out_path.name} ({summary.output_mode.upper()})")
    else:
        lines.append(f"Emitted {summary.output_mode.upper()} to stdout")
    if summary.turnover_state != "n/a":
        verdict = summary.turnover_state.upper()
        lines.append(f"Sample turnover: {100 * summary.turnover_rate:.1f}% — {verdict}")
    if summary.substantive_regroup_state != "n/a":
        lines.append(f"Substantive regroup gate: {summary.substantive_regroup_state.upper()}")

    lines.append("")
    lines.append(f"Done in {summary.elapsed_seconds:.1f}s.")
    return "\n".join(lines) + "\n"


def _column_prefix(version_label: str) -> str:
    return version_label.replace(".", "_")


def _cell(value: object) -> str:
    """Render a cell value for TSV. None / NaN / empty -> sentinel."""
    if value is None:
        return TSV_NULL_SENTINEL
    if isinstance(value, float):
        import math

        if math.isnan(value):
            return TSV_NULL_SENTINEL
    return str(value)

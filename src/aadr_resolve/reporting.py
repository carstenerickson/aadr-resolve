"""Cohort manifest writers. Per LLD §3.14."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import CohortManifest

# Missing-cell sentinel per HLD §Output: cohort manifest TSV.
TSV_NULL_SENTINEL = "--"


def write_cohort_tsv(manifest: CohortManifest, path: Path) -> None:
    """Write the cohort manifest as TSV (HLD §Output: cohort).

    Stable column order:
      cohort_label, cohort_label_source, individual_id_canonical,
      library_token,
      then per-version columns in user-supplied order:
        v{X}_genetic_id, v{X}_group_id, v{X}_snps_hit_1240k,
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
        payload.append(
            {
                "cohort_label": row.cohort_label,
                "cohort_label_source": row.cohort_label_source,
                "individual_id_canonical": row.individual_id_canonical,
                "library_token": row.library_token,
                "per_version_gid": row.per_version_gid,
                "per_version_group_id": row.per_version_group_id,
                "per_version_snps_hit_1240k": row.per_version_snps_hit_1240k,
                "persistent_genetic_id": row.persistent_genetic_id,
                "status": row.status,
            }
        )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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

"""Single-sample resolution. Per LLD §3.13 / HLD §Output: lookup.

Day-3 scope: within-version + simple-equality cross-version matching. The
MID-rename bridge (auto-detected via shared GIDs) lands in Day 4 — until
then, an individual whose Master ID itself was renamed across releases
won't chain through. Day 4 will wire bridge.detect_bridge() into this
module's `lookup_single()`.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from .annoframe import AnnoFrame
from .types import LookupResult, LookupRowRecord, SchemaClass

_MatchedVia = Literal["individual_id", "genetic_id", "not_found"]


def lookup_single(query: str, anno_frames: list[AnnoFrame]) -> LookupResult:
    """Resolve a single individual_id or genetic_id across the supplied versions.

    Resolution priority (LLD §3.13):
      1. Match `query` as `individual_id` in any version. If found, the
         canonical id is the query itself (Day-4: bridge.canonical_id()).
      2. Else try as `genetic_id`. If found, the canonical id is the
         `individual_id` of the matching row(s).
      3. Else: matched_via='not_found'; per_version is empty.

    For each anno_frame, collect ALL rows matching the canonical id under
    its `individual_id` column. Multi-row IIDs (e.g., Loschbour with .AG +
    .DG libraries in v66; UKY001 with 7 rows in v62) all surface.

    Status flags:
      - 'present_in_X_of_Y_versions' where X = versions with ≥1 matching
        row, Y = total supplied versions.
      - 'matched_via_genetic_id' (only when fallback fired).
      - 'multi_row' (when any version has ≥2 matching rows; flags the
        multi-library case for the CLI renderer).
    """
    if not anno_frames:
        return LookupResult(query=query, individual_id_canonical=query, matched_via="not_found")

    canonical_id, matched_via = _resolve_canonical(query, anno_frames)
    if matched_via == "not_found":
        return LookupResult(
            query=query,
            individual_id_canonical=query,
            matched_via="not_found",
            status_flags=["not_found"],
        )

    per_version: dict[str, list[LookupRowRecord]] = {}
    versions_with_rows = 0
    has_multi_row = False
    for af in anno_frames:
        rows = _rows_for_individual(af, canonical_id)
        if rows:
            per_version[af.version] = rows
            versions_with_rows += 1
            if len(rows) > 1:
                has_multi_row = True

    status_flags: list[str] = []
    status_flags.append(f"present_in_{versions_with_rows}_of_{len(anno_frames)}_versions")
    if matched_via == "genetic_id":
        status_flags.append("matched_via_genetic_id")
    if has_multi_row:
        status_flags.append("multi_row")

    return LookupResult(
        query=query,
        individual_id_canonical=canonical_id,
        matched_via=matched_via,
        per_version=per_version,
        status_flags=status_flags,
    )


def _resolve_canonical(query: str, anno_frames: list[AnnoFrame]) -> tuple[str, _MatchedVia]:
    """Return (canonical_id, matched_via).

    Priority: individual_id match first, genetic_id match second, else
    not_found. Day 3 has no MID bridge so the canonical_id is the
    individual_id observed in whichever anno_frame matched first."""
    # First pass: look for individual_id matches.
    for af in anno_frames:
        iid_series = af.individual_id
        if (iid_series == query).any():
            return query, "individual_id"

    # Second pass: look for genetic_id matches.
    for af in anno_frames:
        gid_series = af.genetic_id
        gid_mask = gid_series == query
        if gid_mask.any():
            # Pull this row's individual_id as the canonical id.
            iid_series = af.individual_id
            matching_iid = iid_series[gid_mask].iloc[0]
            return str(matching_iid), "genetic_id"

    return query, "not_found"


def _rows_for_individual(af: AnnoFrame, individual_id: str) -> list[LookupRowRecord]:
    """Return all LookupRowRecords whose individual_id matches."""
    mask = af.individual_id == individual_id
    if not mask.any():
        return []

    gids = af.genetic_id[mask].tolist()
    grps = af.group_id[mask].tolist()
    snps_hit = _safe_int64_column(af, "snps_hit_1240k", mask)
    pgids = _safe_pgid_column(af, mask)

    records: list[LookupRowRecord] = []
    for i, gid in enumerate(gids):
        records.append(
            LookupRowRecord(
                version_label=af.version,
                genetic_id=str(gid),
                group_id=str(grps[i]) if i < len(grps) else "",
                snps_hit_1240k=snps_hit[i] if i < len(snps_hit) else None,
                persistent_genetic_id=pgids[i] if i < len(pgids) else None,
            )
        )
    return records


def _safe_int64_column(af: AnnoFrame, canonical: str, mask: pd.Series) -> list[int | None]:
    """Pull an Int64-nullable column by canonical field; return Python ints
    (or None for <NA>). Returns empty list if the field isn't in the
    schema class."""
    if not af.schema_def.has_field(canonical):
        return []
    from .date_norm import to_int64_nullable

    raw = af._raw_column(canonical)
    typed = to_int64_nullable(raw)
    selected = typed[mask]
    return [None if pd.isna(v) else int(v) for v in selected]


def _safe_pgid_column(af: AnnoFrame, mask: pd.Series) -> list[int | None]:
    """Pull persistent_genetic_id (class E only); return Python ints or None.

    Returns [None, None, ...] for classes A–D (no PGID column)."""
    if af.schema_class != SchemaClass.E:
        return [None] * int(mask.sum())
    pgid_series = af.persistent_genetic_id
    if pgid_series is None:
        return [None] * int(mask.sum())
    selected = pgid_series[mask]
    return [None if pd.isna(v) else int(v) for v in selected]

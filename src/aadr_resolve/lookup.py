"""Single-sample resolution. Per LLD §3.13 / HLD §Output: lookup.

Day-4 scope: full MID-rename bridge wired in. Cross-version queries chain
even when the Master ID itself was renamed (e.g., v54 I0001 → v62
Loschbour, witnessed by the shared GID Loschbour_snpAD.DG).
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from .annoframe import AnnoFrame, ensure_unique_versions
from .types import LookupResult, LookupRowRecord, MIDBridge, SchemaClass

_MatchedVia = Literal["individual_id", "genetic_id", "not_found"]


def lookup_single(
    query: str,
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge | None = None,
) -> LookupResult:
    """Resolve a single individual_id or genetic_id across the supplied versions.

    Resolution priority (LLD §3.13):
      1. Match `query` as `individual_id` in any version. If found, the
         canonical id is bridge.canonical_id(version, query).
      2. Else try as `genetic_id`. If found, the canonical id is
         bridge.canonical_id() applied to the matching row's IID.
      3. Else: matched_via='not_found'.

    For each anno_frame, collect ALL rows where bridge.canonical_id(version,
    iid_in_that_version) == canonical_id. This way the SAME individual
    surfaces across versions even when its MID was renamed (e.g., v54 I0001
    + v62 Loschbour both map to canonical 'Loschbour' via the bridge).

    Status flags:
      - 'present_in_X_of_Y_versions'.
      - 'matched_via_genetic_id' (only when GID fallback fired).
      - 'multi_row' (any version has ≥2 matching rows).
      - 'individual_id_renamed' (≥1 bridge event traversed for this individual)."""
    if not anno_frames:
        return LookupResult(query=query, individual_id_canonical=query, matched_via="not_found")

    # per_version below is keyed by version label; reject duplicates (e.g. v50.0
    # 1240K + v50.0 HO) before one panel's rows silently overwrite the other's.
    ensure_unique_versions(anno_frames)

    if bridge is None:
        bridge = MIDBridge()

    canonical_id, matched_via = _resolve_canonical(query, anno_frames, bridge)
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
    bridge_events_traversed: list[dict[str, str]] = []
    seen_event_keys: set[tuple[str, str, str, str]] = set()
    for af in anno_frames:
        rows = _rows_for_canonical(af, canonical_id, bridge)
        if rows:
            per_version[af.version] = rows
            versions_with_rows += 1
            if len(rows) > 1:
                has_multi_row = True
            # Collect any bridge events whose chain involves this individual + version.
            for event in bridge.events:
                if bridge.canonical_id(event.v_old_label, event.mid_old) != canonical_id:
                    continue
                key = (event.v_old_label, event.mid_old, event.v_new_label, event.mid_new)
                if key in seen_event_keys:
                    continue
                seen_event_keys.add(key)
                bridge_events_traversed.append(
                    {
                        "v_old_label": event.v_old_label,
                        "mid_old": event.mid_old,
                        "v_new_label": event.v_new_label,
                        "mid_new": event.mid_new,
                        "via_genetic_id": event.via_genetic_id or "(manual)",
                    }
                )

    status_flags: list[str] = []
    status_flags.append(f"present_in_{versions_with_rows}_of_{len(anno_frames)}_versions")
    if matched_via == "genetic_id":
        status_flags.append("matched_via_genetic_id")
    if has_multi_row:
        status_flags.append("multi_row")
    if bridge_events_traversed:
        status_flags.append("individual_id_renamed")

    return LookupResult(
        query=query,
        individual_id_canonical=canonical_id,
        matched_via=matched_via,
        master_id_bridge=bridge_events_traversed,
        per_version=per_version,
        status_flags=status_flags,
    )


def _resolve_canonical(
    query: str,
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
) -> tuple[str, _MatchedVia]:
    """Return (canonical_id, matched_via).

    Priority: individual_id match first, genetic_id match second, else
    not_found. The canonical_id is bridge.canonical_id() applied to the
    matching row's IID."""
    # First pass: look for individual_id matches.
    for af in anno_frames:
        iid_series = af.individual_id
        if (iid_series == query).any():
            return bridge.canonical_id(af.version, query), "individual_id"

    # Second pass: look for genetic_id matches.
    for af in anno_frames:
        gid_series = af.genetic_id
        gid_mask = gid_series == query
        if gid_mask.any():
            iid_series = af.individual_id
            matching_iid = str(iid_series[gid_mask].iloc[0])
            return bridge.canonical_id(af.version, matching_iid), "genetic_id"

    return query, "not_found"


def _rows_for_canonical(
    af: AnnoFrame, canonical_id: str, bridge: MIDBridge
) -> list[LookupRowRecord]:
    """Return all LookupRowRecords for rows whose canonical id (per bridge)
    equals `canonical_id`."""
    # Resolve every IID in this AnnoFrame to its canonical form, then pick
    # the rows whose canonical matches the query's canonical.
    iid_series = af.individual_id
    # Vectorized: build a per-row canonical Series via dict lookup.
    canonical_per_row = iid_series.map(
        lambda iid: bridge.canonical_id(af.version, iid) if isinstance(iid, str) else iid
    )
    mask = canonical_per_row == canonical_id
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

    Returns [None, None, ...] for every class but E (no PGID column)."""
    if af.schema_class != SchemaClass.E:
        return [None] * int(mask.sum())
    pgid_series = af.persistent_genetic_id
    if pgid_series is None:
        return [None] * int(mask.sum())
    selected = pgid_series[mask]
    return [None if pd.isna(v) else int(v) for v in selected]

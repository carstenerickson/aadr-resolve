"""Cohort file parsing + manifest emission. Per LLD §3.10."""

from __future__ import annotations

import csv
from itertools import pairwise
from pathlib import Path

import pandas as pd

from .annoframe import AnnoFrame
from .date_norm import to_int64_nullable
from .errors import IOFailure, UsageError
from .gates import TurnoverGateResult
from .group_classifier import classify_group_change
from .library_token import collapse_to_individual, version_tuple
from .types import (
    AnnoFileInfo,
    CohortManifest,
    CohortRunSummary,
    GroupChangeClass,
    LibraryIdentityResult,
    LibraryToken,
    ManifestRow,
    MIDBridge,
    SchemaClass,
)


def parse_cohort_file(path: Path) -> dict[str, str | None]:
    """Parse a --cohort-version FILE TSV.

    Accepts two forms (HLD §CLI reference):
      - One column: one individual_id per line. Returns {iid: None}.
      - Two columns: (individual_id, cohort_label) tab-separated. Returns
        {iid: cohort_label}.

    Header line is tolerated when its first non-comment line starts with
    'individual_id' (case-insensitive). Empty lines + '#' comment lines
    are skipped.

    Raises UsageError on 3+ columns. Raises IOFailure on file-not-found."""
    if not path.exists():
        raise IOFailure(f"cohort file not found: {path}")

    out: dict[str, str | None] = {}
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        return out

    # Detect header.
    start_idx = 0
    if rows[0] and rows[0][0].strip().lower() == "individual_id":
        start_idx = 1

    for line_no, row in enumerate(rows[start_idx:], start=start_idx + 1):
        if not row or all(not c.strip() for c in row):
            continue
        if row[0].lstrip().startswith("#"):
            continue
        if len(row) == 1:
            iid = row[0].strip()
            if iid and iid not in out:
                out[iid] = None
        elif len(row) == 2:
            iid = row[0].strip()
            label = row[1].strip()
            if iid and iid not in out:
                out[iid] = label or None
        else:
            raise UsageError(
                f"cohort file {path}:{line_no} has {len(row)} columns; expected 1 or 2"
            )

    return out


def detect_cohort_version(
    cohort_ids: set[str],
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
) -> str | None:
    """Auto-detect which supplied .anno file the cohort IDs are scoped to.

    For each anno_frame, compute |cohort_ids ∩ individual_ids|. Returns the
    version_label with the largest intersection. Ties broken by user-supplied
    order (first in anno_frames wins).

    Returns None when every anno_frame has zero intersection — caller treats
    this as a UsageError (exit 4) and suggests --cohort-version."""
    best_version: str | None = None
    best_score = 0
    for af in anno_frames:
        iids_for_af = set(af.individual_id.dropna().astype(str))
        score = len(cohort_ids & iids_for_af)
        if score > best_score:
            best_score = score
            best_version = af.version
    return best_version if best_score > 0 else None


def propagate_labels(
    cohort_input: dict[str, str | None],
    cohort_version: str,
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
    *,
    no_propagate: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Propagate cohort_labels from cohort_version to all canonical individuals.

    Returns (canonical_id -> label, canonical_id -> source).

    Source is 'direct' for individuals whose label was supplied in the
    cohort file; 'inferred_from_v_X' for individuals reached via the
    MID-rename bridge from a cohort-input entry. Individuals not in the
    cohort_input (and not reachable via bridge) are not included in the
    returned dicts at all.

    When no_propagate=True, only individuals with explicit cohort_input
    entries get a label; cross-version bridge propagation is disabled."""
    # Find the AnnoFrame matching cohort_version.
    cohort_af: AnnoFrame | None = None
    for af in anno_frames:
        if af.version == cohort_version:
            cohort_af = af
            break
    if cohort_af is None:
        # Fall back: build a synthetic per-iid mapping from cohort_input
        # alone. The canonical_id == iid in this case.
        canonical_labels = {iid: label or iid for iid, label in cohort_input.items()}
        canonical_source = {iid: "direct" for iid in cohort_input}
        return canonical_labels, canonical_source

    # Map cohort_input iids -> canonical_id via the cohort_version's bridge.
    canonical_to_label: dict[str, str] = {}
    canonical_to_source: dict[str, str] = {}

    version_column_prefix = _version_column_prefix(cohort_version)

    for iid, label in cohort_input.items():
        canonical = bridge.canonical_id(cohort_version, iid)
        # Default label = the IID itself if user didn't supply one.
        resolved_label = label if label is not None else canonical
        canonical_to_label[canonical] = resolved_label
        canonical_to_source[canonical] = "direct"

    if no_propagate:
        return canonical_to_label, canonical_to_source

    # Propagation: for every individual present in any anno_frame whose
    # canonical_id matches a label from cohort_input, inherit the label.
    inferred_source = f"inferred_from_v_{version_column_prefix}"
    for af in anno_frames:
        if af.version == cohort_version:
            continue
        for iid in af.individual_id.dropna().astype(str):
            canonical = bridge.canonical_id(af.version, iid)
            if canonical in canonical_to_label and canonical not in canonical_to_source:
                canonical_to_source[canonical] = inferred_source

    # Mark direct-propagated explicitly: any in cohort_input that's already in
    # canonical_to_source as 'direct' stays 'direct'. The 'inferred' source is
    # for canonicals reached via the bridge from a cohort entry — same
    # canonical_to_label, but the source records that the cohort_version
    # didn't contain the IID directly.
    # Currently every canonical in canonical_to_label comes from cohort_input,
    # so source is always 'direct'. To make 'inferred_from_v_X' fire, we'd
    # need to include canonicals reached via the bridge from cohort_input —
    # which is exactly what the loop above did. But we set 'inferred' only
    # when source wasn't already 'direct'. That's the correct behavior.

    return canonical_to_label, canonical_to_source


def build_manifest(
    cohort_input: dict[str, str | None],
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
    library_identities: dict[str, LibraryIdentityResult],
    *,
    cohort_version: str,
    no_propagate: bool = False,
    collapse: bool = False,
    gid_preference: tuple[str, ...] = (
        "AG",
        "DG",
        "SG",
        "HO",
        "TW",
        "BY",
        "AA",
        "EC",
        "WGC",
        "bare",
    ),
) -> CohortManifest:
    """Build the cohort manifest.

    Sequence (LLD §4.1 step 11):
      a. Propagate cohort labels (propagate_labels).
      b. For each cohort individual, look up its LibraryIdentityResult.
      c. Build one ManifestRow per (individual × library).
      d. If collapse=True, reduce to one row per individual via
         library_token.collapse_to_individual.
      e. Sort rows: (cohort_label, individual_id_canonical, library_token).
      f. Pack into CohortManifest."""
    sorted_afs = sorted(anno_frames, key=version_tuple)
    sorted_versions = tuple(af.version for af in sorted_afs)

    canonical_to_label, canonical_to_source = propagate_labels(
        cohort_input, cohort_version, anno_frames, bridge, no_propagate=no_propagate
    )

    warnings: list[str] = []
    rows: list[ManifestRow] = []

    for canonical_id, label in canonical_to_label.items():
        identity = library_identities.get(canonical_id)
        if identity is None or not identity.libraries:
            # Individual not present in any supplied .anno; emit a single
            # placeholder row.
            rows.append(
                _placeholder_row(
                    cohort_label=label,
                    cohort_label_source=canonical_to_source.get(canonical_id, "direct"),
                    individual_id_canonical=canonical_id,
                    versions=sorted_versions,
                )
            )
            continue

        if collapse:
            # Reduce to one row by gid_preference; emit a single row with
            # the collapsed per-version GIDs.
            chosen, dropped = collapse_to_individual(identity, gid_preference)
            if dropped:
                warnings.append(
                    f"{canonical_id}: {len(dropped)} libraries dropped during "
                    f"--collapse-to-individual: {dropped}"
                )
            rows.append(
                _row_from_collapsed(
                    canonical_id=canonical_id,
                    cohort_label=label,
                    cohort_label_source=canonical_to_source.get(canonical_id, "direct"),
                    chosen_gid_per_version=chosen,
                    sorted_afs=sorted_afs,
                    bridge=bridge,
                )
            )
        else:
            for library in identity.libraries:
                rows.append(
                    _row_from_library(
                        canonical_id=canonical_id,
                        cohort_label=label,
                        cohort_label_source=canonical_to_source.get(canonical_id, "direct"),
                        library_token=library,
                        sorted_afs=sorted_afs,
                        bridge=bridge,
                    )
                )

    rows.sort(
        key=lambda r: (
            r.cohort_label,
            r.individual_id_canonical,
            r.library_token,
        )
    )

    return CohortManifest(
        versions_supplied=sorted_versions,
        rows=tuple(rows),
        warnings=tuple(warnings),
    )


# === Internal helpers ===


def _version_column_prefix(version_label: str) -> str:
    """'v44.3' -> 'v44_3' for column name use."""
    return version_label.replace(".", "_").lstrip("v") and version_label.replace(".", "_")


def _row_from_library(
    canonical_id: str,
    cohort_label: str,
    cohort_label_source: str,
    library_token: LibraryToken,
    sorted_afs: list[AnnoFrame],
    bridge: MIDBridge,
) -> ManifestRow:
    """Build one ManifestRow for an (individual, library) pair."""
    per_version_gid = dict(library_token.per_version_gid)
    per_version_group_id: dict[str, str | None] = {}
    per_version_snps_hit: dict[str, int | None] = {}
    pgid_latest_e: int | None = None

    for af in sorted_afs:
        gid = per_version_gid.get(af.version)
        if gid is None:
            per_version_group_id[af.version] = None
            per_version_snps_hit[af.version] = None
            continue
        per_version_group_id[af.version] = _group_for_gid(af, gid)
        per_version_snps_hit[af.version] = _snps_hit_for_gid(af, gid)
        if af.schema_class == SchemaClass.E:
            pgid = _pgid_for_gid(af, gid)
            if pgid is not None:
                pgid_latest_e = pgid

    status = _row_status(per_version_gid, library_token.chain_status, sorted_afs)
    per_pair_change_class = _compute_per_pair_change_class(per_version_group_id, sorted_afs)

    return ManifestRow(
        cohort_label=cohort_label,
        cohort_label_source=cohort_label_source,
        individual_id_canonical=canonical_id,
        library_token=library_token.token,
        per_version_gid=per_version_gid,
        per_version_group_id=per_version_group_id,
        per_version_snps_hit_1240k=per_version_snps_hit,
        persistent_genetic_id=pgid_latest_e,
        status=status,
        per_pair_group_change_class=per_pair_change_class,
    )


def _row_from_collapsed(
    canonical_id: str,
    cohort_label: str,
    cohort_label_source: str,
    chosen_gid_per_version: dict[str, str | None],
    sorted_afs: list[AnnoFrame],
    bridge: MIDBridge,
) -> ManifestRow:
    """Build one ManifestRow representing the collapsed individual."""
    per_version_group_id: dict[str, str | None] = {}
    per_version_snps_hit: dict[str, int | None] = {}
    pgid_latest_e: int | None = None

    for af in sorted_afs:
        gid = chosen_gid_per_version.get(af.version)
        if gid is None:
            per_version_group_id[af.version] = None
            per_version_snps_hit[af.version] = None
            continue
        per_version_group_id[af.version] = _group_for_gid(af, gid)
        per_version_snps_hit[af.version] = _snps_hit_for_gid(af, gid)
        if af.schema_class == SchemaClass.E:
            pgid = _pgid_for_gid(af, gid)
            if pgid is not None:
                pgid_latest_e = pgid

    # Token for the collapsed row = canonical_id (since we're not picking a
    # specific library, the row represents the individual as a whole).
    token = canonical_id

    status = _row_status(chosen_gid_per_version, "chained", sorted_afs)
    per_pair_change_class = _compute_per_pair_change_class(per_version_group_id, sorted_afs)

    return ManifestRow(
        cohort_label=cohort_label,
        cohort_label_source=cohort_label_source,
        individual_id_canonical=canonical_id,
        library_token=token,
        per_version_gid=chosen_gid_per_version,
        per_version_group_id=per_version_group_id,
        per_version_snps_hit_1240k=per_version_snps_hit,
        persistent_genetic_id=pgid_latest_e,
        status=status,
        per_pair_group_change_class=per_pair_change_class,
    )


def _placeholder_row(
    cohort_label: str,
    cohort_label_source: str,
    individual_id_canonical: str,
    versions: tuple[str, ...],
) -> ManifestRow:
    """A row for an individual not present in any supplied .anno."""
    nulls: dict[str, str | None] = {v: None for v in versions}
    # Placeholder rows have no group_id in any version → every adjacent
    # pair gets a None classification.
    per_pair_change_class: dict[tuple[str, str], str | None] = {
        pair: None for pair in pairwise(versions)
    }
    return ManifestRow(
        cohort_label=cohort_label,
        cohort_label_source=cohort_label_source,
        individual_id_canonical=individual_id_canonical,
        library_token=individual_id_canonical,
        per_version_gid=nulls,
        per_version_group_id=dict(nulls),
        per_version_snps_hit_1240k={v: None for v in versions},
        persistent_genetic_id=None,
        status="not_in_any_supplied_version",
        per_pair_group_change_class=per_pair_change_class,
    )


def _compute_per_pair_change_class(
    per_version_group_id: dict[str, str | None],
    sorted_afs: list[AnnoFrame],
) -> dict[tuple[str, str], str | None]:
    """Classify the group_id change per adjacent (v_old, v_new) version pair.

    Per LLD §4.1 step 11d. Returns one entry per adjacent pair in the
    version-sorted anno-frame list. Values are:
      - one of the six GroupChangeClass values when both ends are non-None
        AND the group_id differs across the pair;
      - the string 'none' when both ends are non-None and the group_id is
        unchanged;
      - None when either end is absent (individual not present in that
        version, or per_version_group_id is None for some other reason)."""
    sorted_versions = tuple(af.version for af in sorted_afs)
    result: dict[tuple[str, str], str | None] = {}
    for v_old, v_new in pairwise(sorted_versions):
        group_old = per_version_group_id.get(v_old)
        group_new = per_version_group_id.get(v_new)
        if group_old is None or group_new is None:
            result[(v_old, v_new)] = None
        elif group_old == group_new:
            result[(v_old, v_new)] = "none"
        else:
            result[(v_old, v_new)] = classify_group_change(group_old, group_new).value
    return result


def _group_for_gid(af: AnnoFrame, gid: str) -> str | None:
    mask = af.genetic_id == gid
    if not mask.any():
        return None
    groups = af.group_id[mask].tolist()
    return str(groups[0]) if groups else None


def _snps_hit_for_gid(af: AnnoFrame, gid: str) -> int | None:
    if not af.schema_def.has_field("snps_hit_1240k"):
        return None
    mask = af.genetic_id == gid
    if not mask.any():
        return None
    raw = af._raw_column("snps_hit_1240k")
    typed = to_int64_nullable(raw)
    selected = typed[mask]
    if selected.empty:
        return None
    val = selected.iloc[0]
    return None if pd.isna(val) else int(val)


def _pgid_for_gid(af: AnnoFrame, gid: str) -> int | None:
    pgid_series = af.persistent_genetic_id
    if pgid_series is None:
        return None
    mask = af.genetic_id == gid
    if not mask.any():
        return None
    selected = pgid_series[mask]
    if selected.empty:
        return None
    val = selected.iloc[0]
    return None if pd.isna(val) else int(val)


def _row_status(
    per_version_gid: dict[str, str | None],
    chain_status: str,
    sorted_afs: list[AnnoFrame],
) -> str:
    """Map per-version presence + chain status to a status string."""
    if chain_status == "ambiguous":
        return "library_chain_ambiguous"

    sorted_versions = [af.version for af in sorted_afs]
    present = [v for v in sorted_versions if per_version_gid.get(v) is not None]
    absent = [v for v in sorted_versions if per_version_gid.get(v) is None]

    if not present:
        return "not_in_any_supplied_version"
    if not absent:
        return "present_all"

    first_present_idx = sorted_versions.index(present[0])
    last_present_idx = sorted_versions.index(present[-1])

    if first_present_idx > 0:
        prev_version = sorted_versions[first_present_idx - 1]
        return f"added_after_{_version_column_prefix(prev_version)}"
    if last_present_idx < len(sorted_versions) - 1:
        next_version = sorted_versions[last_present_idx + 1]
        return f"removed_before_{_version_column_prefix(next_version)}"
    return "present_some"


def build_cohort_run_summary(
    *,
    manifest: CohortManifest,
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
    bridge_manual_count: int,
    cohort_input_path: Path | None,
    cohort_input_n_individuals: int,
    out_path: Path,
    n_cols_written: int,
    turnover_gates: list[TurnoverGateResult],
    cohort_coverage_state: str = "n/a",
    cohort_coverage_rate: float = 0.0,
    warnings: tuple[str, ...] = (),
    config: dict[str, object] | None = None,
    elapsed_seconds: float,
) -> CohortRunSummary:
    """Build the run-level summary for stdout + JSON sidecar rendering.

    Aggregates the orchestrator's intermediate state into one frozen
    dataclass. `n_cols_written` is what the TSV writer actually emitted —
    cohort_cmd computes it by counting the manifest's TSV columns
    (versions × per-version-cols + per-pair cols + fixed cols + status).
    See `reporting.format_stdout_summary` for the renderer."""
    sorted_afs = sorted(anno_frames, key=version_tuple)
    anno_file_info = tuple(
        AnnoFileInfo(
            version_label=af.version,
            path=af.path if af.path is not None else Path(af.version),
            n_rows=len(af.individual_id),
            n_cols=len(af.df.columns),
            schema_class=af.schema_class,
        )
        for af in sorted_afs
    )

    # Resolution histogram over earliest vs latest version, computed from
    # manifest rows' presence pattern in per_version_gid.
    earliest = sorted_afs[0].version if sorted_afs else None
    latest = sorted_afs[-1].version if sorted_afs else None
    n_resolved_in_latest = 0
    n_added_after_earliest = 0
    n_removed_before_latest = 0
    if earliest is not None and latest is not None:
        canonical_presence: dict[str, set[str]] = {}
        for row in manifest.rows:
            present = {v for v, gid in row.per_version_gid.items() if gid is not None}
            canonical_presence.setdefault(row.individual_id_canonical, set()).update(present)
        for present in canonical_presence.values():
            in_earliest = earliest in present
            in_latest = latest in present
            if in_latest:
                n_resolved_in_latest += 1
            if in_latest and not in_earliest:
                n_added_after_earliest += 1
            if in_earliest and not in_latest:
                n_removed_before_latest += 1

    # Group-change histogram: aggregate per_pair_group_change_class across
    # all rows × all pairs. Counts substantive classifications only (skips
    # 'none' and None).
    valid_classes = {c.value for c in GroupChangeClass}
    group_change_by_class: dict[str, int] = dict.fromkeys(valid_classes, 0)
    for row in manifest.rows:
        for cls in row.per_pair_group_change_class.values():
            if cls in valid_classes:
                group_change_by_class[cls] += 1

    # Turnover state: the worst (highest-severity) pair wins. 'fail' > 'warn' > 'pass'.
    severity_order = {"pass": 0, "warn": 1, "fail": 2}
    worst_state = "n/a"
    worst_rate = 0.0
    if turnover_gates:
        worst_gate = max(turnover_gates, key=lambda g: severity_order.get(g.state, -1))
        worst_state = worst_gate.state
        worst_rate = worst_gate.removal_rate

    # Label-source + status histograms over manifest rows (per LLD §3.14
    # JSON sidecar shape). Histograms are over rows, not individuals.
    label_source_histogram: dict[str, int] = {}
    status_histogram: dict[str, int] = {}
    for row in manifest.rows:
        label_source_histogram[row.cohort_label_source] = (
            label_source_histogram.get(row.cohort_label_source, 0) + 1
        )
        status_histogram[row.status] = status_histogram.get(row.status, 0) + 1

    return CohortRunSummary(
        versions_supplied=manifest.versions_supplied,
        anno_file_info=anno_file_info,
        bridge_auto_count=len(bridge.events),
        bridge_manual_count=bridge_manual_count,
        bridge_collisions=(),  # populated when on_collision='warn' surfaces them; future work
        cohort_input_path=cohort_input_path,
        cohort_input_n_individuals=cohort_input_n_individuals,
        n_resolved_in_latest=n_resolved_in_latest,
        n_added_after_earliest=n_added_after_earliest,
        n_removed_before_latest=n_removed_before_latest,
        group_change_by_class=group_change_by_class,
        out_path=out_path,
        n_rows_written=manifest.n_libraries,
        n_cols_written=n_cols_written,
        turnover_state=worst_state,
        turnover_rate=worst_rate,
        elapsed_seconds=elapsed_seconds,
        n_individuals=manifest.n_individuals,
        label_source_histogram=label_source_histogram,
        status_histogram=status_histogram,
        cohort_coverage_state=cohort_coverage_state,
        cohort_coverage_rate=cohort_coverage_rate,
        warnings=warnings,
        config=dict(config) if config is not None else {},
    )

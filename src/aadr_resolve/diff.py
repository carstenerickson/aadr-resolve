"""Cross-version diff. Per LLD §3.11 / HLD §Output: diff."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .annoframe import AnnoFrame
from .gates import SubstantiveRegroupGateResult, TurnoverGateResult
from .group_classifier import classify_group_change
from .types import (
    AnnoFileInfo,
    DiffEvent,
    DiffResult,
    DiffRunSummary,
    GroupChangeClass,
    MIDBridge,
)


def compute_diff(
    af_old: AnnoFrame,
    af_new: AnnoFrame,
    bridge: MIDBridge | None = None,
) -> DiffResult:
    """Compute a structured diff between two .anno versions.

    Sequence (LLD §4.2 / §3.11):
      1. Build canonical individual_id sets for both versions via bridge.
      2. added = new_canonical - old_canonical
         removed = old_canonical - new_canonical
         shared = old_canonical & new_canonical
      3. For each shared individual:
         a. Compare GID sets per individual; if disjoint and non-empty
            in both, record `genetic_id_renamed`.
         b. master_id_renamed: extract bridge.events where v_old/v_new
            match this pair.
         c. Compute majority group_id per individual per version;
            classify the change via group_classifier.
      4. Build DiffResult.

    Day-5 scope: gates dict is empty (Day-7 will populate exit-1 gate
    evaluations: turnover above threshold, substantive-regroup over
    threshold, schema low-confidence)."""
    if bridge is None:
        bridge = MIDBridge()

    # Canonical individual maps per version.
    canonical_to_rows_old = _canonical_id_to_row_indices(af_old, bridge)
    canonical_to_rows_new = _canonical_id_to_row_indices(af_new, bridge)
    canonicals_old = set(canonical_to_rows_old)
    canonicals_new = set(canonical_to_rows_new)
    shared = canonicals_old & canonicals_new
    added_set = canonicals_new - canonicals_old
    removed_set = canonicals_old - canonicals_new

    # Added.
    added: list[DiffEvent] = []
    for canonical in sorted(added_set):
        first_gid = _first_gid_for_canonical(af_new, canonical_to_rows_new[canonical])
        added.append(
            DiffEvent(
                event_class="added",
                individual_id_canonical=canonical,
                details={"first_seen_genetic_id": first_gid},
            )
        )

    # Removed.
    removed: list[DiffEvent] = []
    for canonical in sorted(removed_set):
        last_gid = _first_gid_for_canonical(af_old, canonical_to_rows_old[canonical])
        removed.append(
            DiffEvent(
                event_class="removed",
                individual_id_canonical=canonical,
                details={"last_seen_genetic_id": last_gid},
            )
        )

    # Genetic ID renamed (within shared individuals, disjoint GID sets).
    genetic_id_renamed: list[DiffEvent] = []
    for canonical in sorted(shared):
        gids_old = _gid_set(af_old, canonical_to_rows_old[canonical])
        gids_new = _gid_set(af_new, canonical_to_rows_new[canonical])
        if gids_old and gids_new and gids_old.isdisjoint(gids_new):
            genetic_id_renamed.append(
                DiffEvent(
                    event_class="genetic_id_renamed",
                    individual_id_canonical=canonical,
                    details={
                        "v_old_gids": sorted(gids_old),
                        "v_new_gids": sorted(gids_new),
                    },
                )
            )

    # Master ID renamed (from bridge events).
    master_id_renamed: list[DiffEvent] = []
    for event in bridge.events:
        if event.v_old_label == af_old.version and event.v_new_label == af_new.version:
            canonical = bridge.canonical_id(event.v_new_label, event.mid_new)
            master_id_renamed.append(
                DiffEvent(
                    event_class="master_id_renamed",
                    individual_id_canonical=canonical,
                    details={
                        "v_old_mid": event.mid_old,
                        "v_new_mid": event.mid_new,
                        "via_genetic_id": event.via_genetic_id,
                    },
                )
            )

    # Group ID change classification (per individual; majority group_id).
    group_changed_by_class: dict[GroupChangeClass, list[DiffEvent]] = {
        c: [] for c in GroupChangeClass
    }
    for canonical in sorted(shared):
        group_old = _majority_group_id(af_old, canonical_to_rows_old[canonical])
        group_new = _majority_group_id(af_new, canonical_to_rows_new[canonical])
        if not group_old or not group_new or group_old == group_new:
            continue
        cls = classify_group_change(group_old, group_new)
        group_changed_by_class[cls].append(
            DiffEvent(
                event_class="group_changed",
                individual_id_canonical=canonical,
                details={
                    "group_v_old": group_old,
                    "group_v_new": group_new,
                    "change_class": cls.value,
                },
            )
        )

    return DiffResult(
        v_old_label=af_old.version,
        v_old_class=af_old.schema_class,
        v_old_n_individuals=len(canonicals_old),
        v_new_label=af_new.version,
        v_new_class=af_new.schema_class,
        v_new_n_individuals=len(canonicals_new),
        shared_individuals=len(shared),
        added=added,
        removed=removed,
        genetic_id_renamed=genetic_id_renamed,
        master_id_renamed=master_id_renamed,
        group_changed_by_class=group_changed_by_class,
        gates={},
    )


# === Internal helpers ===


def _canonical_id_to_row_indices(af: AnnoFrame, bridge: MIDBridge) -> dict[str, list[int]]:
    """Map canonical individual_id -> list of row indices in af."""
    out: dict[str, list[int]] = {}
    iids = af.individual_id.tolist()
    for row_idx, iid in enumerate(iids):
        if not isinstance(iid, str) or not iid:
            continue
        canonical = bridge.canonical_id(af.version, iid)
        out.setdefault(canonical, []).append(row_idx)
    return out


def _first_gid_for_canonical(af: AnnoFrame, row_indices: list[int]) -> str:
    """Return the first GID among the given rows."""
    if not row_indices:
        return ""
    gids = af.genetic_id.tolist()
    return str(gids[row_indices[0]]) if row_indices[0] < len(gids) else ""


def _gid_set(af: AnnoFrame, row_indices: list[int]) -> set[str]:
    """Return the set of GIDs at the given row indices."""
    if not row_indices:
        return set()
    gids = af.genetic_id.tolist()
    return {str(gids[i]) for i in row_indices if i < len(gids) and gids[i]}


def _majority_group_id(af: AnnoFrame, row_indices: list[int]) -> str:
    """Compute the majority group_id across the given rows.

    Per LLD §3.11 pin: a single individual with multi-row data (multiple
    libraries) may have multiple group_id values in each version. The
    classifier consumes the MAJORITY group_id per individual per version.
    Ties broken alphabetically — deterministic across runs.

    Empty/blank group_ids are excluded from the vote."""
    if not row_indices:
        return ""
    groups = af.group_id.tolist()
    candidates: list[str] = [str(groups[i]) for i in row_indices if i < len(groups) and groups[i]]
    if not candidates:
        return ""
    counter = Counter(candidates)
    # Sort by (-count, group) so ties break alphabetically.
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return top[0][0]


def build_diff_run_summary(
    *,
    result: DiffResult,
    af_old: AnnoFrame,
    af_new: AnnoFrame,
    bridge: MIDBridge,
    bridge_manual_count: int,
    out_path: Path | None,
    output_mode: str,
    turnover_gate: TurnoverGateResult,
    substantive_regroup_gate: SubstantiveRegroupGateResult,
    elapsed_seconds: float,
) -> DiffRunSummary:
    """Build the run-level summary for the diff stdout block + the v0.2
    A2 --report-json sidecar.

    Mirrors `cohort.build_cohort_run_summary` shape; diff has its own
    event counts instead of a cohort-input section. The substantive-
    regroup gate has a state of 'n/a' when the threshold is unset (HLD
    default — gate disabled)."""
    anno_file_info = tuple(
        AnnoFileInfo(
            version_label=af.version,
            path=af.path if af.path is not None else Path(af.version),
            n_rows=len(af.individual_id),
            n_cols=len(af.df.columns),
            schema_class=af.schema_class,
        )
        for af in (af_old, af_new)
    )

    valid_classes = {c.value for c in GroupChangeClass}
    group_change_by_class: dict[str, int] = dict.fromkeys(valid_classes, 0)
    for cls, events in result.group_changed_by_class.items():
        group_change_by_class[cls.value] = len(events)

    regroup_state = (
        "n/a" if substantive_regroup_gate.threshold is None else substantive_regroup_gate.state
    )

    return DiffRunSummary(
        versions_supplied=(result.v_old_label, result.v_new_label),
        anno_file_info=anno_file_info,
        bridge_auto_count=len(bridge.events),
        bridge_manual_count=bridge_manual_count,
        bridge_collisions=(),
        n_added=len(result.added),
        n_removed=len(result.removed),
        n_genetic_id_renamed=len(result.genetic_id_renamed),
        n_master_id_renamed=len(result.master_id_renamed),
        group_change_by_class=group_change_by_class,
        out_path=out_path,
        output_mode=output_mode,
        turnover_state=turnover_gate.state,
        turnover_rate=turnover_gate.removal_rate,
        substantive_regroup_state=regroup_state,
        elapsed_seconds=elapsed_seconds,
    )

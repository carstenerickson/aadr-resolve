"""Exit-1 validation gates. Per HLD §Exit-1 validation gates + LLD §4.1
step 13 / §4.2.

Gate (a) — sample turnover — is the common one across both `cohort` and
`diff`. Both subcommands evaluate it after the manifest/result is on
disk so the user has the data even when the gate fires.

The gate fires per consecutive-version pair; for a 2-version `diff`
that's a single pair, for a multi-version `cohort` it's all adjacent
pairs and the worst wins."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from .types import CohortManifest, DiffResult, GroupChangeClass, MIDBridge

GateState = Literal["pass", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class TurnoverGateResult:
    """One pair's turnover gate evaluation."""

    v_old: str
    v_new: str
    removal_rate: float
    state: GateState


def evaluate_turnover_diff(
    result: DiffResult,
    *,
    turnover_warn: float = 0.05,
    turnover_fail: float = 0.30,
) -> TurnoverGateResult:
    """Single-pair gate for `diff`. Uses DiffResult.removal_rate directly."""
    rate = result.removal_rate
    state: GateState = "pass"
    if rate >= turnover_fail:
        state = "fail"
    elif rate >= turnover_warn:
        state = "warn"
    return TurnoverGateResult(
        v_old=result.v_old_label,
        v_new=result.v_new_label,
        removal_rate=rate,
        state=state,
    )


def evaluate_turnover_cohort(
    manifest: CohortManifest,
    *,
    turnover_warn: float = 0.05,
    turnover_fail: float = 0.30,
) -> list[TurnoverGateResult]:
    """Per-consecutive-pair gate for `cohort`.

    For each adjacent (v_old, v_new) pair in manifest.versions_supplied,
    compute removal_rate = (individuals present in v_old but absent in
    v_new) / (individuals present in v_old). Returns one
    TurnoverGateResult per pair."""
    versions = manifest.versions_supplied
    if len(versions) < 2:
        return []

    # individual -> set of versions in which the individual has at least
    # one non-None GID. Built from the manifest rows.
    individual_versions: dict[str, set[str]] = {}
    for row in manifest.rows:
        present_versions = {v for v, gid in row.per_version_gid.items() if gid is not None}
        if not present_versions:
            continue
        canonical = row.individual_id_canonical
        individual_versions.setdefault(canonical, set()).update(present_versions)

    results: list[TurnoverGateResult] = []
    for v_old, v_new in pairwise(versions):
        v_old_individuals = {iid for iid, vs in individual_versions.items() if v_old in vs}
        removed = {iid for iid in v_old_individuals if v_new not in individual_versions[iid]}
        rate = len(removed) / len(v_old_individuals) if v_old_individuals else 0.0
        state: GateState = "pass"
        if rate >= turnover_fail:
            state = "fail"
        elif rate >= turnover_warn:
            state = "warn"
        results.append(
            TurnoverGateResult(
                v_old=v_old,
                v_new=v_new,
                removal_rate=rate,
                state=state,
            )
        )
    return results


def format_gate_message(gate: TurnoverGateResult, *, warn_pct: float, fail_pct: float) -> str:
    """Human-readable one-liner per fired gate. Used by command handlers."""
    return (
        f"sample turnover gate ({gate.state}): "
        f"{gate.v_old} -> {gate.v_new}: "
        f"removed {100 * gate.removal_rate:.1f}% "
        f"(warn={100 * warn_pct:.1f}%, fail={100 * fail_pct:.1f}%)"
    )


# === Gate (b): substantive-regroup (diff-only) ===


@dataclass(frozen=True, slots=True)
class SubstantiveRegroupGateResult:
    """Diff-only gate fired when substantive_regroup count exceeds a
    user-supplied threshold. Default behavior (threshold=None): always
    'pass'."""

    count: int
    threshold: int | None
    state: GateState


def evaluate_substantive_regroup_gate(
    result: DiffResult,
    *,
    fail_threshold: int | None = None,
) -> SubstantiveRegroupGateResult:
    """Return the gate result. With fail_threshold=None (HLD default —
    gate disabled), state is always 'pass'."""
    count = len(result.group_changed_by_class.get(GroupChangeClass.SUBSTANTIVE_REGROUP, []))
    state: GateState = "pass"
    if fail_threshold is not None and count > fail_threshold:
        state = "fail"
    return SubstantiveRegroupGateResult(count=count, threshold=fail_threshold, state=state)


def format_substantive_regroup_message(gate: SubstantiveRegroupGateResult) -> str:
    """Human-readable rendering for the diff-only substantive-regroup gate."""
    return (
        f"substantive regroup gate ({gate.state}): "
        f"{gate.count} substantive_regroup events "
        f"exceeds threshold of {gate.threshold}"
    )


# === Gate (d): cohort-coverage (cohort-only) ===


@dataclass(frozen=True, slots=True)
class CohortCoverageGateResult:
    """Cohort-only gate fired when the fraction of cohort_input
    individuals resolved (i.e. landing in the manifest with at least one
    non-empty per-version GID) drops below configured thresholds."""

    resolved: int
    requested: int
    coverage: float
    state: GateState


def evaluate_cohort_coverage_gate(
    cohort_input: dict[str, str | None],
    manifest: CohortManifest,
    *,
    bridge: MIDBridge | None = None,
    cohort_version: str | None = None,
    coverage_warn: float = 0.50,
    coverage_fail: float = 0.25,
) -> CohortCoverageGateResult:
    """Compute coverage = (resolved / requested) where resolved is the
    number of cohort_input individuals whose canonical form appears in
    the manifest with at least one non-None per-version GID. Empty
    cohort_input returns coverage=1.0 (vacuous pass).

    `bridge` + `cohort_version` enable IID-to-canonical mapping so that
    a cohort entry like 'I0001' (the v54 MID) counts as resolved when
    the manifest carries the bridge-canonical 'Loschbour' row. If either
    is None, falls back to raw IID equality (gate may under-count when
    the cohort file uses pre-rename IIDs)."""
    requested = len(cohort_input)
    if requested == 0:
        return CohortCoverageGateResult(resolved=0, requested=0, coverage=1.0, state="pass")

    resolved_canonicals: set[str] = set()
    for row in manifest.rows:
        if any(gid is not None for gid in row.per_version_gid.values()):
            resolved_canonicals.add(row.individual_id_canonical)

    def _canonical(iid: str) -> str:
        if bridge is not None and cohort_version is not None:
            return bridge.canonical_id(cohort_version, iid)
        return iid

    resolved_count = sum(1 for iid in cohort_input if _canonical(iid) in resolved_canonicals)
    coverage = resolved_count / requested

    state: GateState = "pass"
    if coverage < coverage_fail:
        state = "fail"
    elif coverage < coverage_warn:
        state = "warn"
    return CohortCoverageGateResult(
        resolved=resolved_count,
        requested=requested,
        coverage=coverage,
        state=state,
    )


def format_cohort_coverage_message(
    gate: CohortCoverageGateResult,
    *,
    warn_pct: float,
    fail_pct: float,
) -> str:
    """Human-readable rendering for the cohort-coverage gate."""
    return (
        f"cohort coverage gate ({gate.state}): "
        f"{gate.resolved}/{gate.requested} resolved "
        f"({100 * gate.coverage:.1f}%; warn<{100 * warn_pct:.0f}%, "
        f"fail<{100 * fail_pct:.0f}%)"
    )

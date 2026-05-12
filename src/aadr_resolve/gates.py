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

from .types import CohortManifest, DiffResult

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

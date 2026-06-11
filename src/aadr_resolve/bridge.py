"""MID-rename detection + manual override. Per LLD §3.7 / HLD §MID rename detection."""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Literal

from .annoframe import AnnoFrame
from .errors import CollisionDetected, IOFailure
from .types import MIDBridge, MIDRenameEvent

OnMidCollision = Literal["error", "warn"]


def detect_bridge(
    anno_frames: list[AnnoFrame],
    *,
    on_collision: OnMidCollision = "error",
) -> MIDBridge:
    """Auto-detect MID renames across consecutive version pairs.

    Algorithm (HLD §MID rename detection / LLD §3.7):
      1. Sort anno_frames by version_label numerically.
      2. For each consecutive pair (af_old, af_new):
         build gid_to_mid for each; for every shared GID, if the mid differs,
         record a MIDRenameEvent keyed on that GID.
      3. Detect cross-lab collisions: a single (v_old, mid_old) mapping to
         multiple distinct mid_new values.
      4. Build the canonical-id index by walking events in version order and
         propagating each chain to the canonical (latest) MID.

    Returns a populated MIDBridge. Raises CollisionDetected when
    on_collision='error' and a cross-lab collision is found.

    Note: this does NOT reject frames sharing a version label — `diff`/`join`
    legitimately compare two same-version frames positionally. The N-frame
    version-keyed flows (cohort, lookup) call `ensure_unique_versions` instead."""
    if not anno_frames:
        return MIDBridge()

    # Sort by parsed (major, minor) tuple from version_label.
    sorted_afs = sorted(anno_frames, key=lambda af: _parse_version_tuple(af.version))
    canonical_version = sorted_afs[-1].version

    events: list[MIDRenameEvent] = []
    # Track (v_old, mid_old) -> set of (mid_new) for cross-lab collision detection.
    forward_targets: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    # ^^^ {(v_old, mid_old): {mid_new: shared_gid_witness}}

    for af_old, af_new in pairwise(sorted_afs):
        gid_to_mid_old = _build_gid_to_mid(af_old)
        gid_to_mid_new = _build_gid_to_mid(af_new)
        shared_gids = set(gid_to_mid_old) & set(gid_to_mid_new)
        for gid in sorted(shared_gids):
            mid_old = gid_to_mid_old[gid]
            mid_new = gid_to_mid_new[gid]
            if mid_old == mid_new:
                continue
            event = MIDRenameEvent(
                v_old_label=af_old.version,
                mid_old=mid_old,
                v_new_label=af_new.version,
                mid_new=mid_new,
                via_genetic_id=gid,
            )
            events.append(event)
            # Record for collision check (dedup by (v_old, mid_old, mid_new)).
            forward_targets[(af_old.version, mid_old)].setdefault(mid_new, gid)

    # Cross-lab collision detection.
    collisions: list[tuple[str, str, list[str]]] = []
    for (v_old, mid_old), mid_news in forward_targets.items():
        if len(mid_news) > 1:
            af_new_version = _earliest_collision_partner(v_old, mid_news, sorted_afs)
            collisions.append((v_old, mid_old, sorted(mid_news.keys())))
            if on_collision == "error":
                # Raise eagerly with the first collision (others would also fire on retry).
                raise CollisionDetected(
                    v_old=v_old,
                    mid_old=mid_old,
                    v_new=af_new_version,
                    mids_new=sorted(mid_news.keys()),
                )

    if collisions and on_collision == "warn":
        for v_old, mid_old, mid_news_list in collisions:
            sys.stderr.write(
                f"WARNING: cross-lab MID collision: {v_old} MID {mid_old!r} maps to "
                f"multiple individuals: {mid_news_list!r}. Continuing with the "
                f"alphabetically-first mid_new; affected rows will carry "
                f"status=library_chain_ambiguous in the manifest.\n"
            )

    # Build the canonical-id index.
    sorted_versions = [af.version for af in sorted_afs]
    fwd, rev = _build_canonical_indices(events, sorted_versions)

    return MIDBridge(
        events=events,
        _fwd=fwd,
        _rev=rev,
        canonical_version=canonical_version,
    )


def load_manual_bridge(path: Path) -> list[MIDRenameEvent]:
    """Parse a --mid-bridge FILE TSV per HLD §MID rename detection.

    Format:
      Header line: v_old_label	mid_old	v_new_label	mid_new
      One data row per manual entry. Empty / '#' comment lines skipped.

    Returns a list of MIDRenameEvent (via_genetic_id is None for manual entries)."""
    if not path.exists():
        raise IOFailure(f"--mid-bridge file not found: {path}")

    events: list[MIDRenameEvent] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        rows = list(reader)
    if not rows:
        return events

    expected_header = ["v_old_label", "mid_old", "v_new_label", "mid_new"]
    header = rows[0]
    if [h.strip() for h in header] != expected_header:
        raise IOFailure(
            f"--mid-bridge TSV at {path} has malformed header. Expected "
            f"{expected_header!r}, got {header!r}."
        )
    for line_no, row in enumerate(rows[1:], start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if row[0].lstrip().startswith("#"):
            continue
        if len(row) != 4:
            raise IOFailure(
                f"--mid-bridge TSV at {path}:{line_no} has {len(row)} columns; expected 4"
            )
        v_old, mid_old, v_new, mid_new = (c.strip() for c in row)
        events.append(
            MIDRenameEvent(
                v_old_label=v_old,
                mid_old=mid_old,
                v_new_label=v_new,
                mid_new=mid_new,
                via_genetic_id=None,
            )
        )
    return events


def merge_with_overrides(
    auto_bridge: MIDBridge,
    overrides: list[MIDRenameEvent],
) -> tuple[MIDBridge, list[str]]:
    """Layer manual overrides on top of an auto-detected MIDBridge.

    Behavior:
      - Each override is added to the bridge's events list.
      - On (v_old, mid_old) conflict (auto detected X→Y; override says X→Z),
        the override wins. The auto event is REMOVED; the override is added.
        A warning string is generated naming the replacement.
      - Indices are rebuilt from the merged event set.

    Returns (merged_bridge, warnings). Caller emits warnings to stderr."""
    if not overrides:
        return auto_bridge, []

    warnings: list[str] = []
    merged_events: list[MIDRenameEvent] = list(auto_bridge.events)
    auto_by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, e in enumerate(merged_events):
        auto_by_key[(e.v_old_label, e.mid_old)].append(i)

    indices_to_drop: set[int] = set()
    for override in overrides:
        key = (override.v_old_label, override.mid_old)
        for auto_idx in auto_by_key.get(key, []):
            auto_event = merged_events[auto_idx]
            if (
                auto_event.mid_new != override.mid_new
                or auto_event.v_new_label != override.v_new_label
            ):
                warnings.append(
                    f"manual override: {override.v_old_label} {override.mid_old!r} -> "
                    f"{override.mid_new!r} (v_new={override.v_new_label}) replaces "
                    f"auto-detected -> {auto_event.mid_new!r} (v_new={auto_event.v_new_label})"
                )
                indices_to_drop.add(auto_idx)

    final_events = [e for i, e in enumerate(merged_events) if i not in indices_to_drop]
    final_events.extend(overrides)

    # Rebuild indices.
    # Determine canonical_version from the existing bridge (preserves original).
    canonical_version = auto_bridge.canonical_version
    sorted_versions = sorted(
        {auto_bridge.canonical_version}
        | {e.v_old_label for e in final_events}
        | {e.v_new_label for e in final_events},
        key=_parse_version_tuple,
    )
    if sorted_versions:
        canonical_version = sorted_versions[-1]
    fwd, rev = _build_canonical_indices(final_events, sorted_versions)
    return (
        MIDBridge(
            events=final_events,
            _fwd=fwd,
            _rev=rev,
            canonical_version=canonical_version,
        ),
        warnings,
    )


def compute_canonical_version(version_labels: list[str]) -> str:
    """Return the latest version label by numeric (major, minor) tuple.

    Future-proofs against `v100.0` AADR releases (lex-sort puts v100 before
    v44). Defensive against unparseable labels (returns (0, 0) for those,
    sorting them first)."""
    if not version_labels:
        return ""
    return max(version_labels, key=_parse_version_tuple)


# === Internal helpers ===


_VERSION_RE = re.compile(r"v(\d+)\.(\d+)")


def _parse_version_tuple(label: str) -> tuple[int, int]:
    """'v66.0' -> (66, 0). Returns (0, 0) for unparseable labels."""
    m = _VERSION_RE.fullmatch(label)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (0, 0)


def _build_gid_to_mid(af: AnnoFrame) -> dict[str, str]:
    """Build a GID -> Individual ID mapping for one AnnoFrame.

    For individuals with multiple rows (different libraries), each row's GID
    is a separate key. Last-occurrence wins on duplicate GIDs within the
    same .anno (which shouldn't happen in practice — schema invariant)."""
    gids = af.genetic_id.tolist()
    iids = af.individual_id.tolist()
    out: dict[str, str] = {}
    for gid, iid in zip(gids, iids, strict=True):
        if isinstance(gid, str) and gid:
            out[gid] = str(iid)
    return out


def _earliest_collision_partner(
    v_old: str,
    mid_news: dict[str, str],
    sorted_afs: list[AnnoFrame],
) -> str:
    """For a collision report, identify the v_new label where the collision
    surfaced. Returns the earliest v_new among the affected events."""
    versions = sorted(
        {af.version for af in sorted_afs if af.version != v_old},
        key=_parse_version_tuple,
    )
    return versions[0] if versions else v_old


def _build_canonical_indices(
    events: list[MIDRenameEvent],
    sorted_versions: list[str],
) -> tuple[
    dict[tuple[str, str], str],
    dict[str, set[tuple[str, str]]],
]:
    """Build the forward (version, mid) -> canonical_mid index and the
    reverse canonical_mid -> set of (version, mid) index.

    Algorithm: union-find over (version, mid) nodes using events as edges.
    Each connected component's canonical mid is the mid in the latest version
    among the component's members."""
    if not events:
        return {}, {}

    # Union-find.
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(node: tuple[str, str]) -> tuple[str, str]:
        if node not in parent:
            parent[node] = node
            return node
        # Path compression.
        root = node
        while parent[root] != root:
            root = parent[root]
        # Compress.
        cur = node
        while parent[cur] != root:
            parent[cur], cur = root, parent[cur]
        return root

    def union(a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in events:
        union((e.v_old_label, e.mid_old), (e.v_new_label, e.mid_new))

    # Group by component.
    components: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for node in parent:
        components[find(node)].append(node)

    # Canonical mid per component = mid in the LATEST version among members.
    version_rank = {v: i for i, v in enumerate(sorted_versions)}

    fwd: dict[tuple[str, str], str] = {}
    rev: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for component_nodes in components.values():
        # Pick the (version, mid) with the highest version rank.
        latest_node = max(
            component_nodes,
            key=lambda n: (version_rank.get(n[0], -1), n[0], n[1]),
        )
        canonical_mid = latest_node[1]
        for node in component_nodes:
            fwd[node] = canonical_mid
            rev[canonical_mid].add(node)
    return fwd, dict(rev)

"""aadr-resolve: AADR cross-version GeneticID / MasterID join utility."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import MIDBridge

__version__ = "0.2.0.dev0"

from .annoframe import AnnoFrame
from .bridge import detect_bridge
from .errors import (
    AadrResolveError,
    CollisionDetected,
    InvariantViolation,
    IOFailure,
    MissingNativeFieldError,
    SchemaDetectionError,
    UsageError,
    ValidationError,
)

__all__ = [  # noqa: RUF022 — grouped intentionally (data type, API, errors, version)
    # Core data type
    "AnnoFrame",
    # Library API top-level functions
    "resolve_genetic_ids",
    "resolve_master_ids",
    # Exception hierarchy (aadr-subset etc. catch + re-raise these)
    "AadrResolveError",
    "CollisionDetected",
    "InvariantViolation",
    "IOFailure",
    "MissingNativeFieldError",
    "SchemaDetectionError",
    "UsageError",
    "ValidationError",
    # Version
    "__version__",
]


def resolve_master_ids(
    ids: list[str],
    src_version: str,
    dst_version: str,
    *,
    anno_paths: dict[str, Path | str] | None = None,
    anno_frames: dict[str, AnnoFrame] | None = None,
    mid_bridge: Path | str | None = None,
) -> dict[str, str | None]:
    """Resolve a list of individual_ids from src_version to dst_version
    GeneticIDs through the MID-rename bridge.

    Per HLD §Library API surface. For each input id, returns its
    representative GeneticID in dst_version (the alphabetically-first
    among the matching rows for that individual). None if the individual
    is absent from dst_version.

    Either `anno_paths` (dict version_label -> Path) OR `anno_frames`
    (dict version_label -> AnnoFrame) must be supplied.

    `mid_bridge` (Path or str to a TSV file) supplies manual MID-rename
    entries that layer on top of the GID-stable auto-detection — the
    same semantics as the `--mid-bridge` CLI flag. Format: header
    `v_old_label\\tmid_old\\tv_new_label\\tmid_new`."""
    afs = _resolve_anno_frames(anno_paths, anno_frames, src_version, dst_version)
    bridge = _build_bridge(list(afs.values()), mid_bridge)
    af_dst = afs[dst_version]
    iids_dst = af_dst.individual_id.tolist()
    gids_dst = af_dst.genetic_id.tolist()

    out: dict[str, str | None] = {}
    for query in ids:
        canonical = bridge.canonical_id(src_version, query)
        # Find rows in dst_version whose canonical individual_id matches.
        matches: list[str] = []
        for iid, gid in zip(iids_dst, gids_dst, strict=True):
            if not isinstance(iid, str) or not iid:
                continue
            if bridge.canonical_id(dst_version, iid) == canonical:
                matches.append(str(gid))
        out[query] = sorted(matches)[0] if matches else None
    return out


def resolve_genetic_ids(
    ids: list[str],
    src_version: str,
    dst_version: str,
    *,
    anno_paths: dict[str, Path | str] | None = None,
    anno_frames: dict[str, AnnoFrame] | None = None,
    mid_bridge: Path | str | None = None,
) -> dict[str, list[str]]:
    """Resolve a list of GeneticIDs from src_version to ALL dst_version
    GeneticIDs that share the same individual.

    Multi-row-per-IID semantics preserved: one input GeneticID maps to a
    list (may be multi-row if the individual has multiple libraries in
    dst_version).

    `mid_bridge` (Path or str): optional manual MID-rename override TSV;
    same semantics as the `--mid-bridge` CLI flag.

    Empty list if no rows in dst_version share the individual."""
    afs = _resolve_anno_frames(anno_paths, anno_frames, src_version, dst_version)
    bridge = _build_bridge(list(afs.values()), mid_bridge)
    af_src = afs[src_version]
    af_dst = afs[dst_version]

    # src GID -> src IID via af_src.
    src_iids = af_src.individual_id.tolist()
    src_gids = af_src.genetic_id.tolist()
    src_gid_to_iid: dict[str, str] = {}
    for iid, gid in zip(src_iids, src_gids, strict=True):
        if isinstance(gid, str) and gid and isinstance(iid, str) and iid:
            src_gid_to_iid[gid] = iid

    # dst canonical -> list of dst GIDs.
    dst_iids = af_dst.individual_id.tolist()
    dst_gids = af_dst.genetic_id.tolist()
    dst_canonical_to_gids: dict[str, list[str]] = {}
    for iid, gid in zip(dst_iids, dst_gids, strict=True):
        if not isinstance(iid, str) or not iid or not isinstance(gid, str) or not gid:
            continue
        canonical = bridge.canonical_id(dst_version, iid)
        dst_canonical_to_gids.setdefault(canonical, []).append(str(gid))

    out: dict[str, list[str]] = {}
    for query in ids:
        src_iid = src_gid_to_iid.get(query)
        if src_iid is None:
            out[query] = []
            continue
        canonical = bridge.canonical_id(src_version, src_iid)
        out[query] = sorted(dst_canonical_to_gids.get(canonical, []))
    return out


def _build_bridge(
    anno_frames: list[AnnoFrame],
    mid_bridge: Path | str | None,
) -> MIDBridge:
    """Build a MIDBridge from anno_frames, layering in manual overrides if
    `mid_bridge` is supplied. Internal helper shared by the resolve_*
    functions."""
    bridge = detect_bridge(anno_frames)
    if mid_bridge is not None:
        from .bridge import load_manual_bridge, merge_with_overrides

        overrides = load_manual_bridge(Path(mid_bridge))
        bridge, _warnings = merge_with_overrides(bridge, overrides)
    return bridge


def _resolve_anno_frames(
    anno_paths: dict[str, Path | str] | None,
    anno_frames: dict[str, AnnoFrame] | None,
    src_version: str,
    dst_version: str,
) -> dict[str, AnnoFrame]:
    if anno_frames is not None:
        if src_version not in anno_frames or dst_version not in anno_frames:
            raise KeyError(f"anno_frames must contain both {src_version!r} and {dst_version!r}")
        return anno_frames
    if anno_paths is not None:
        if src_version not in anno_paths or dst_version not in anno_paths:
            raise KeyError(f"anno_paths must contain both {src_version!r} and {dst_version!r}")
        return {
            label: AnnoFrame.from_path(Path(path), version_label=label)
            for label, path in anno_paths.items()
        }
    raise ValueError("either anno_paths or anno_frames must be supplied")

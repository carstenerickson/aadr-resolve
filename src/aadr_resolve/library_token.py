"""Cross-version library identity (Rules A + B). Per LLD §3.8 / HLD §Library-token.

Within an individual_id_canonical scope, group GIDs by suffix class
(.AG/.DG/.SG/.HO/.TW/.BY/.AA/.EC/.WGC/bare) and apply two pairing rules:

  Rule A — bare-to-suffixed promotion. When a version has a bare-numeric
    GID with stem X and a later version has a suffixed GID with the same
    stem X (in any suffix class), they're the same library. Captures the
    v44->v62 AADR convention shift (`I0001` -> `I0001.AG`).

  Rule B — same-suffix-class single-library bridge. When a suffix class
    has exactly one GID in each of two versions with different stems,
    they're the same library — the stem changed because the MID itself
    renamed. Captures `I0001.AG` (v62) -> `Loschbour.AG` (v66).

Trivial pairing also applies: identical (stem, suffix_class) across
versions is always the same library.
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import pairwise

from .annoframe import AnnoFrame
from .types import LibraryIdentityResult, LibraryToken, MIDBridge

_KNOWN_SUFFIXES: tuple[str, ...] = (
    "AG",
    "DG",
    "SG",
    "HO",
    "TW",
    "BY",
    "AA",
    "EC",
    "WGC",
)
_SUFFIX_RE: re.Pattern[str] = re.compile(r"^(.+?)\.(" + "|".join(_KNOWN_SUFFIXES) + r")$")


def parse_gid(gid: str) -> tuple[str, str | None]:
    """Split a GID into (stem, suffix). Suffix is one of _KNOWN_SUFFIXES or
    None for bare-numeric IDs.

    Examples:
      'I0001' -> ('I0001', None)
      'I0001.AG' -> ('I0001', 'AG')
      'Loschbour.AG' -> ('Loschbour', 'AG')
      'Loschbour_snpAD.DG' -> ('Loschbour_snpAD', 'DG')"""
    m = _SUFFIX_RE.match(gid)
    if m:
        return m.group(1), m.group(2)
    return gid, None


def build_library_identity(  # noqa: PLR0912, PLR0915 (chain algorithm; splitting hurts clarity)
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
    individual_id_canonical: str,
) -> LibraryIdentityResult:
    """For one individual, compute LibraryToken chain across all versions.

    Algorithm:
      1. Per version, gather GIDs whose canonical IID equals individual_id_canonical.
      2. Parse each GID into (stem, suffix).
      3. Use union-find to merge (version, GID) nodes into library chains:
         - Identical (stem, suffix) within a suffix class across versions: chain.
         - Rule A: bare-stem GID in v_old + same-stem suffixed GID in v_new
           when no other suffixed-with-that-stem exists in v_old AND no
           bare-with-that-stem exists in v_new: chain them.
         - Rule B: suffix class has exactly 1 GID per version (across
           supplied versions) AND the stems differ: chain them.
      4. Each connected component becomes one LibraryToken; token = the
         most-recent-version GID in the component."""
    sorted_afs = sorted(anno_frames, key=version_tuple)
    sorted_versions = [af.version for af in sorted_afs]
    version_rank = {v: i for i, v in enumerate(sorted_versions)}

    # Step 1: per-version GIDs for this individual.
    per_version_entries: dict[str, list[tuple[str, str, str | None]]] = {}
    for af in sorted_afs:
        per_version_entries[af.version] = _entries_for_individual(
            af, bridge, individual_id_canonical
        )

    if not any(per_version_entries.values()):
        return LibraryIdentityResult(
            individual_id_canonical=individual_id_canonical,
            libraries=(),
        )

    # Nodes: (version, gid). Build union-find.
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(node: tuple[str, str]) -> tuple[str, str]:
        if node not in parent:
            parent[node] = node
            return node
        root = node
        while parent[root] != root:
            root = parent[root]
        cur = node
        while parent[cur] != root:
            parent[cur], cur = root, parent[cur]
        return root

    def union(a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Pre-populate parents from every observed node.
    for v, entries in per_version_entries.items():
        for gid, _stem, _suffix in entries:
            find((v, gid))

    # Trivial-merge: identical (stem, suffix) across versions.
    by_stem_suffix: dict[tuple[str, str | None], list[tuple[str, str]]] = defaultdict(list)
    for v, entries in per_version_entries.items():
        for gid, stem, suffix in entries:
            by_stem_suffix[(stem, suffix)].append((v, gid))
    for nodes in by_stem_suffix.values():
        for i in range(1, len(nodes)):
            union(nodes[0], nodes[i])

    # Rule A: bare-stem v_old + matching-stem suffixed v_new.
    # For each (v_old, v_new) adjacent pair, look at bare GIDs in v_old
    # and suffixed GIDs in v_new with matching stems.
    for af_old, af_new in _adjacent_pairs(sorted_afs):
        bare_old: dict[str, tuple[str, str]] = {}  # stem -> (version, gid)
        suffixed_old_stems: set[str] = set()
        for gid, stem, suffix in per_version_entries[af_old.version]:
            if suffix is None:
                bare_old[stem] = (af_old.version, gid)
            else:
                suffixed_old_stems.add(stem)

        suffixed_new: dict[str, list[tuple[str, str]]] = defaultdict(list)
        bare_new_stems: set[str] = set()
        for gid, stem, suffix in per_version_entries[af_new.version]:
            if suffix is None:
                bare_new_stems.add(stem)
            else:
                suffixed_new[stem].append((af_new.version, gid))

        for stem, node_old in bare_old.items():
            if stem in bare_new_stems:
                continue  # bare-to-bare handled by trivial merge
            if stem in suffixed_old_stems:
                continue  # v_old already has both bare AND suffixed with this stem
            matches = suffixed_new.get(stem, [])
            if len(matches) == 1:
                union(node_old, matches[0])

    # Rule B: same-suffix-class with single-library-per-class in BOTH versions
    # and differing stems.
    for af_old, af_new in _adjacent_pairs(sorted_afs):
        by_suffix_old: dict[str | None, list[tuple[str, str, str | None]]] = defaultdict(list)
        by_suffix_new: dict[str | None, list[tuple[str, str, str | None]]] = defaultdict(list)
        for gid, stem, suffix in per_version_entries[af_old.version]:
            by_suffix_old[suffix].append((gid, stem, suffix))
        for gid, stem, suffix in per_version_entries[af_new.version]:
            by_suffix_new[suffix].append((gid, stem, suffix))

        for suffix, old_list in by_suffix_old.items():
            new_list = by_suffix_new.get(suffix, [])
            if len(old_list) == 1 and len(new_list) == 1:
                old_gid, old_stem, _ = old_list[0]
                new_gid, new_stem, _ = new_list[0]
                if old_stem != new_stem:
                    union((af_old.version, old_gid), (af_new.version, new_gid))

    # Build connected components.
    components: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for node in parent:
        components[find(node)].append(node)

    libraries: list[LibraryToken] = []
    for nodes in components.values():
        # Token = the GID from the LATEST version in the component.
        latest_node = max(nodes, key=lambda n: (version_rank.get(n[0], -1), n[0], n[1]))
        token = latest_node[1]

        per_version_gid: dict[str, str | None] = {v: None for v in sorted_versions}
        # If multiple GIDs from the same version, deterministically pick the
        # alphabetically-first (ambiguity flag is set elsewhere).
        is_ambiguous = False
        node_by_version: dict[str, list[str]] = defaultdict(list)
        for v, gid in nodes:
            node_by_version[v].append(gid)
        for v, gids_at_v in node_by_version.items():
            if len(gids_at_v) > 1:
                is_ambiguous = True
                per_version_gid[v] = sorted(gids_at_v)[0]
            else:
                per_version_gid[v] = gids_at_v[0]

        n_versions_present = sum(1 for v in per_version_gid.values() if v is not None)
        if is_ambiguous:
            status: str = "ambiguous"
        elif n_versions_present > 1:
            status = "chained"
        else:
            status = "orphan"

        libraries.append(
            LibraryToken(
                token=token,
                per_version_gid=per_version_gid,
                chain_status=status,  # type: ignore[arg-type]
            )
        )

    libraries.sort(key=lambda lt: lt.token)
    has_ambiguous = any(lt.chain_status == "ambiguous" for lt in libraries)

    return LibraryIdentityResult(
        individual_id_canonical=individual_id_canonical,
        libraries=tuple(libraries),
        has_ambiguous=has_ambiguous,
    )


def build_all_library_identities(
    anno_frames: list[AnnoFrame],
    bridge: MIDBridge,
) -> dict[str, LibraryIdentityResult]:
    """Compute library identities for every individual across all anno_frames.

    Returns a dict keyed by individual_id_canonical."""
    # Collect all canonical individuals across all versions.
    all_canonicals: set[str] = set()
    for af in anno_frames:
        for iid in af.individual_id.tolist():
            if isinstance(iid, str) and iid:
                all_canonicals.add(bridge.canonical_id(af.version, iid))

    return {
        canonical: build_library_identity(anno_frames, bridge, canonical)
        for canonical in sorted(all_canonicals)
    }


def collapse_to_individual(
    identity: LibraryIdentityResult,
    gid_preference: tuple[str, ...],
) -> tuple[dict[str, str | None], list[str]]:
    """Reduce a LibraryIdentityResult to one-row-per-individual representation.

    For each version, pick the LibraryToken whose suffix class has the highest
    priority in `gid_preference`. 'bare' in the preference list matches GIDs
    with suffix=None.

    Returns (chosen_gid_per_version, dropped_library_tokens). The dropped list
    enumerates the LibraryTokens that lost to the preference; the caller emits
    a stderr warning naming the count."""
    if not identity.libraries:
        return {}, []

    # Establish all versions in the identity.
    all_versions: set[str] = set()
    for lt in identity.libraries:
        all_versions.update(lt.per_version_gid.keys())
    sorted_versions = sorted(all_versions, key=version_tuple)

    # For each version, score each library by gid_preference; pick the best.
    chosen_per_version: dict[str, str | None] = {v: None for v in sorted_versions}
    chosen_library_per_version: dict[str, LibraryToken | None] = {v: None for v in sorted_versions}

    for v in sorted_versions:
        best: LibraryToken | None = None
        best_score: tuple[int, str] | None = None
        for lt in identity.libraries:
            gid = lt.per_version_gid.get(v)
            if gid is None:
                continue
            _, suffix = parse_gid(gid)
            key = suffix if suffix is not None else "bare"
            try:
                rank = gid_preference.index(key)
            except ValueError:
                rank = len(gid_preference)  # unknown suffixes sort last
            score = (rank, gid)  # rank first, alpha tiebreak
            if best_score is None or score < best_score:
                best = lt
                best_score = score
                chosen_per_version[v] = gid
        chosen_library_per_version[v] = best

    # Tokens that won at least one version.
    won_tokens: set[str] = {
        lt.token for lt in chosen_library_per_version.values() if lt is not None
    }
    dropped = [lt.token for lt in identity.libraries if lt.token not in won_tokens]

    return chosen_per_version, dropped


# === Internal helpers ===


_VERSION_RE = re.compile(r"v(\d+)\.(\d+)")


def version_tuple(af_or_label: AnnoFrame | str) -> tuple[int, int]:
    """Parse version label or AnnoFrame.version into a (major, minor) tuple.
    Returns (0, 0) for unparseable labels — sorts unknowns first."""
    label = af_or_label.version if isinstance(af_or_label, AnnoFrame) else af_or_label
    m = _VERSION_RE.fullmatch(label)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (0, 0)


def _adjacent_pairs(
    sorted_afs: list[AnnoFrame],
) -> list[tuple[AnnoFrame, AnnoFrame]]:
    """Return consecutive (af_old, af_new) pairs."""
    return list(pairwise(sorted_afs))


def _entries_for_individual(
    af: AnnoFrame,
    bridge: MIDBridge,
    individual_id_canonical: str,
) -> list[tuple[str, str, str | None]]:
    """Return [(gid, stem, suffix), ...] for rows whose canonical IID matches."""
    iids = af.individual_id.tolist()
    gids = af.genetic_id.tolist()
    out: list[tuple[str, str, str | None]] = []
    for iid, gid in zip(iids, gids, strict=True):
        if not isinstance(iid, str) or not iid or not isinstance(gid, str) or not gid:
            continue
        if bridge.canonical_id(af.version, iid) != individual_id_canonical:
            continue
        stem, suffix = parse_gid(gid)
        out.append((gid, stem, suffix))
    return out

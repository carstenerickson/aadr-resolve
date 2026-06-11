"""Seven-class Group ID change classifier. Per LLD §3.9.

Pure functions; no I/O, no side effects. The classifier walks classes in a
FIXED ORDER (suffix → country → prefix_drop → order → punct → partial →
substantive). First match wins. Reordering would change results — pinned by
the HLD."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .types import GroupChangeClass

# Suffix classes per HLD §Library-token. Matches the `_KNOWN_SUFFIXES` in
# the bench-verify schema YAMLs.
_KNOWN_SUFFIX_RE: re.Pattern[str] = re.compile(r"\.(AG|DG|SG|HO|TW|BY|AA|EC|WGC)$")

# Country-rename known-list. Bench-verify found only Czech → Czechia;
# future entries land here when surfaced in real AADR releases. The map
# is module-private; extending it doesn't change the public API.
_COUNTRY_RENAMES: dict[str, str] = {
    "Czech": "Czechia",
}

# Author/lab prefixes dropped wholesale in AADR v66. In v44 many groups
# carried a `Patterson_` prefix (Patterson 2022); v66 strips it. Extend this
# set if future releases drop other prefixes.
_KNOWN_PREFIX_DROPS: frozenset[str] = frozenset({"Patterson"})


def classify_group_change(  # noqa: PLR0911 (7 classes, one return each — see docstring)
    group_old: str, group_new: str
) -> GroupChangeClass:
    """Classify a single (group_old, group_new) change event.

    Algorithm walks classes in priority order; first match wins:
      1. convention_restructure_suffix
      2. convention_restructure_country
      3. convention_restructure_prefix_drop
      4. convention_restructure_order
      5. convention_restructure_punct
      6. partial
      7. substantive_regroup (catchall)

    Pure function; no exceptions for normal inputs."""
    # 1. Suffix-only change.
    stripped_old = _strip_known_suffix(group_old)
    stripped_new = _strip_known_suffix(group_new)
    if stripped_old == stripped_new and group_old != group_new:
        return GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX

    # 2. Country rename. Suffix-strip first so `Czech_IA.AG` → `Czechia_IA`
    # is reachable.
    if _matches_country_rename(stripped_old, stripped_new):
        return GroupChangeClass.CONVENTION_RESTRUCTURE_COUNTRY

    # 3. Known author/lab prefix drop (e.g. Patterson_England_IA → England_IA).
    if _matches_prefix_drop(stripped_old, stripped_new):
        return GroupChangeClass.CONVENTION_RESTRUCTURE_PREFIX_DROP

    # 4. Token-order change (same tokens, different order).
    tokens_old = stripped_old.split("_")
    tokens_new = stripped_new.split("_")
    if sorted(tokens_old) == sorted(tokens_new) and tokens_old != tokens_new:
        return GroupChangeClass.CONVENTION_RESTRUCTURE_ORDER

    # 5. Punctuation-only change (- vs _ swap).
    punct_normalized_old = stripped_old.replace("-", "_")
    punct_normalized_new = stripped_new.replace("-", "_")
    if punct_normalized_old == punct_normalized_new and stripped_old != stripped_new:
        return GroupChangeClass.CONVENTION_RESTRUCTURE_PUNCT

    # 6. Partial: one side's tokens are a proper subset/superset of the other.
    set_old = set(tokens_old)
    set_new = set(tokens_new)
    if set_old != set_new and (set_old <= set_new or set_new <= set_old):
        return GroupChangeClass.PARTIAL

    # 7. Catchall.
    return GroupChangeClass.SUBSTANTIVE_REGROUP


def suggest_group_lift(group_id: str) -> str | None:
    """Return the suggested v66 group ID for a v44-era group ID, or None.

    Applies suffix stripping then checks each registered prefix drop. If the
    group ID carries a known dropped prefix, returns the bare form. Returns
    None when no lift is known — the caller should treat the group ID as-is.

    Examples::

        suggest_group_lift("Patterson_England_IA")  # → "England_IA"
        suggest_group_lift("Patterson_England_IA.AG")  # → "England_IA"
        suggest_group_lift("England_IA")  # → None (no lift needed)
    """
    stripped = _strip_known_suffix(group_id)
    for prefix in _KNOWN_PREFIX_DROPS:
        leader = prefix + "_"
        if stripped.startswith(leader):
            return stripped[len(leader) :]
    return None


def classify_all(
    pairs: Iterable[tuple[str, str]],
) -> dict[GroupChangeClass, list[tuple[str, str]]]:
    """Vectorized classifier over many (group_old, group_new) pairs.

    Returns a 6-key dict (every GroupChangeClass present, possibly empty).
    Identical-input pairs are skipped (they're not changes)."""
    out: dict[GroupChangeClass, list[tuple[str, str]]] = {c: [] for c in GroupChangeClass}
    for pair in pairs:
        group_old, group_new = pair
        if group_old == group_new:
            continue
        cls = classify_group_change(group_old, group_new)
        out[cls].append(pair)
    return out


# === Internal helpers ===


def _strip_known_suffix(group: str) -> str:
    """Remove a trailing .AG/.DG/.SG/.HO/.TW/.BY/.AA/.EC/.WGC if present."""
    return _KNOWN_SUFFIX_RE.sub("", group)


def _matches_prefix_drop(stripped_old: str, stripped_new: str) -> bool:
    """Return True if `stripped_old` becomes `stripped_new` by removing a
    known author/lab prefix.  E.g. Patterson_England_IA → England_IA."""
    for prefix in _KNOWN_PREFIX_DROPS:
        leader = prefix + "_"
        if stripped_old.startswith(leader) and stripped_old[len(leader) :] == stripped_new:
            return True
    return False


def _matches_country_rename(stripped_old: str, stripped_new: str) -> bool:
    """Try each registered country rename. Returns True if `stripped_old`
    becomes `stripped_new` after substituting one of the known country
    pairs at the start of the token."""
    for old_c, new_c in _COUNTRY_RENAMES.items():
        if stripped_old == old_c:
            if stripped_new == new_c:
                return True
            continue
        if stripped_old.startswith(old_c + "_"):
            substituted = new_c + stripped_old[len(old_c) :]
            if substituted == stripped_new:
                return True
    return False

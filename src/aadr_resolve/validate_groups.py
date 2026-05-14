"""Group ID validation against supplied .anno panel versions. Per LLD §3.17."""

from __future__ import annotations

from dataclasses import dataclass

from .annoframe import AnnoFrame
from .group_classifier import suggest_group_lift


@dataclass(frozen=True)
class GroupValidationItem:
    """Result for one queried group ID.

    status values:
      "valid"        – literal group_id exists in at least one panel version.
      "lifted"       – not found literally; a known lift maps it to `lifted_to`
                       which was found in the panel.
      "unresolvable" – not found and no known lift applies."""

    group_id: str
    status: str
    lifted_to: str | None = None
    found_in: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupValidationResult:
    """Aggregate result for a validate_groups() call."""

    items: tuple[GroupValidationItem, ...]

    @property
    def lifted(self) -> list[GroupValidationItem]:
        return [i for i in self.items if i.status == "lifted"]

    @property
    def unresolvable(self) -> list[GroupValidationItem]:
        return [i for i in self.items if i.status == "unresolvable"]

    @property
    def has_failures(self) -> bool:
        return bool(self.unresolvable)


def validate_groups(
    group_ids: list[str],
    anno_frames: list[AnnoFrame],
) -> GroupValidationResult:
    """Validate each group_id against the supplied .anno panel versions.

    Per-group resolution order:
      1. Literal match in any anno_frame → valid.
      2. suggest_group_lift() finds a candidate that exists in any anno_frame
         → lifted (caller should warn the user).
      3. Neither → unresolvable (caller should warn and may exit non-zero).

    Pure function over the supplied data; no I/O."""
    version_groups: dict[str, set[str]] = {
        af.version: set(af.group_id.dropna().unique()) for af in anno_frames
    }

    items: list[GroupValidationItem] = []
    for gid in group_ids:
        found_literal = [v for v, gs in version_groups.items() if gid in gs]
        if found_literal:
            items.append(
                GroupValidationItem(
                    group_id=gid,
                    status="valid",
                    found_in=tuple(found_literal),
                )
            )
            continue

        candidate = suggest_group_lift(gid)
        if candidate is not None:
            found_lifted = [v for v, gs in version_groups.items() if candidate in gs]
            if found_lifted:
                items.append(
                    GroupValidationItem(
                        group_id=gid,
                        status="lifted",
                        lifted_to=candidate,
                        found_in=tuple(found_lifted),
                    )
                )
                continue

        items.append(GroupValidationItem(group_id=gid, status="unresolvable"))

    return GroupValidationResult(items=tuple(items))

"""Shared dataclasses and enums.

Per LLD §2. Day-1 scope: SchemaClass, ExitCode, FieldMapping, SchemaClassDef.
Day-3 additions: LookupResult, LookupRowRecord. The rest of the §2 types
(MIDBridge, LibraryToken, DiffResult, etc.) land in Day 4+ when the code
consuming them lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Literal


class SchemaClass(Enum):
    """One of five bench-verified schema classes (HLD §`.anno` schema registry)."""

    A = "A"  # v44.3, v50.0; has Index col; "Version ID" at col 2
    B = "B"  # v52.2; has Index col; "Genetic ID" at col 2
    C = "C"  # v54.1; Index dropped; "Genetic ID" at col 1
    D = "D"  # v62.0; same as C with cols added back
    E = "E"  # v66.0; Master ID renamed to Individual ID; new Persistent Genetic ID col 2


class ExitCode(IntEnum):
    """Stable across versions per HLD §Exit codes."""

    OK = 0
    VALIDATION_FAILURE = 1
    IO_FAILURE = 2
    INVARIANT_VIOLATION = 3
    USAGE_ERROR = 4


class GroupChangeClass(Enum):
    """Six-class classifier for Group ID changes (HLD §Group ID change classifier).

    Order matters: walked top-to-bottom by classify_group_change; first match
    wins."""

    CONVENTION_RESTRUCTURE_SUFFIX = "convention_restructure_suffix"
    CONVENTION_RESTRUCTURE_COUNTRY = "convention_restructure_country"
    CONVENTION_RESTRUCTURE_ORDER = "convention_restructure_order"
    CONVENTION_RESTRUCTURE_PUNCT = "convention_restructure_punct"
    PARTIAL = "partial"
    SUBSTANTIVE_REGROUP = "substantive_regroup"


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """One canonical field's location within a schema class.

    Per LLD §2.3.
    """

    canonical_name: str
    column: int  # 1-indexed column position
    normalized_header: str
    display_header: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaClassDef:
    """One schema class loaded from its YAML."""

    class_id: SchemaClass
    applies_to: tuple[str, ...]
    n_columns_set: tuple[int, ...]
    detection_signature: tuple[str, str]
    fields: dict[str, FieldMapping]
    notes: tuple[str, ...]
    not_present: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaClassDef:
        """Parse a YAML-loaded dict into the dataclass.

        Validates required keys; raises ValueError on missing or malformed
        structure (caller wraps in InvariantViolation for a clean exit code)."""
        try:
            class_id = SchemaClass(data["class_id"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"malformed schema YAML: bad class_id: {e}") from e

        applies_to = tuple(data.get("applies_to", []))

        n_cols_raw = data.get("n_columns")
        if n_cols_raw is None:
            raise ValueError(f"schema YAML for class {class_id.value} missing n_columns")
        n_columns_set: tuple[int, ...] = (
            (n_cols_raw,) if isinstance(n_cols_raw, int) else tuple(int(x) for x in n_cols_raw)
        )

        sig = data.get("detection_signature", {})
        detection_signature = (
            str(sig.get("col_0_normalized", "")),
            str(sig.get("col_1_normalized", "")),
        )

        fields_raw = data.get("fields", {})
        fields: dict[str, FieldMapping] = {}
        for canonical, mapping in fields_raw.items():
            fields[canonical] = FieldMapping(
                canonical_name=canonical,
                column=int(mapping["column"]),
                normalized_header=str(mapping["normalized_header"]),
                display_header=mapping.get("display_header"),
            )

        notes = tuple(data.get("notes", []))
        not_present = tuple(data.get("not_present", []))

        return cls(
            class_id=class_id,
            applies_to=applies_to,
            n_columns_set=n_columns_set,
            detection_signature=detection_signature,
            fields=fields,
            notes=notes,
            not_present=not_present,
        )

    def has_field(self, canonical: str) -> bool:
        return canonical in self.fields

    def column_for(self, canonical: str) -> int:
        """Return the 1-indexed column position for a canonical field.

        Raises MissingNativeFieldError (deferred import) if absent.
        """
        if canonical not in self.fields:
            from .errors import MissingNativeFieldError

            raise MissingNativeFieldError(
                f"field {canonical!r} not present in schema class "
                f"{self.class_id.value} (applies to {list(self.applies_to)})"
            )
        return self.fields[canonical].column


# === Day-4: MID-rename bridge types ===


@dataclass(frozen=True, slots=True)
class MIDRenameEvent:
    """One detected (or manually-supplied) Master/Individual-ID rename across
    two versions. Per LLD §2.5.

    `via_genetic_id` is the shared Genetic ID that triggered the auto-detection;
    None for manually-supplied bridge entries."""

    v_old_label: str
    mid_old: str
    v_new_label: str
    mid_new: str
    via_genetic_id: str | None = None


@dataclass
class MIDBridge:
    """Auto-detected MID renames + manual override entries.

    O(1) cross-version canonical-id lookup via the _fwd index. The canonical
    version is the latest version among supplied .anno files; the canonical
    id for an individual is its MID in that latest version."""

    events: list[MIDRenameEvent] = field(default_factory=list)
    # (version_label, mid) -> canonical mid (the latest-version MID for the chain).
    _fwd: dict[tuple[str, str], str] = field(default_factory=dict, repr=False, compare=False)
    # canonical_mid -> set of (version_label, mid_in_that_version) pairs.
    _rev: dict[str, set[tuple[str, str]]] = field(default_factory=dict, repr=False, compare=False)
    canonical_version: str = ""

    def canonical_id(self, version_label: str, mid: str) -> str:
        """Translate (version_label, mid) to the canonical individual_id.

        Unknown (version, mid) pairs fall through to the input mid itself —
        the individual exists in only one supplied version and is its own
        canonical id."""
        return self._fwd.get((version_label, mid), mid)

    def events_for(self, version_label: str, mid: str) -> list[MIDRenameEvent]:
        """Return all rename events whose chain includes (version_label, mid).

        Used by the lookup renderer to populate LookupResult.master_id_bridge."""
        canonical = self.canonical_id(version_label, mid)
        return [
            e
            for e in self.events
            if self._fwd.get((e.v_old_label, e.mid_old)) == canonical
            and self._fwd.get((e.v_new_label, e.mid_new)) == canonical
        ]


# === Day-5: diff result types ===


@dataclass(frozen=True, slots=True)
class DiffEvent:
    """One change event between two versions.

    Per LLD §2.7. `details` payload varies by event_class:
      - 'added' / 'removed': {'first_seen_genetic_id' / 'last_seen_genetic_id': str}
      - 'genetic_id_renamed': {'v_old_gids': list[str], 'v_new_gids': list[str]}
      - 'master_id_renamed': {'v_old_mid': str, 'v_new_mid': str, 'via_genetic_id': str}
      - 'group_changed': {'group_v_old': str, 'group_v_new': str,
                          'change_class': GroupChangeClass.value}"""

    event_class: Literal[
        "added", "removed", "genetic_id_renamed", "master_id_renamed", "group_changed"
    ]
    individual_id_canonical: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffResult:
    """Structured cross-version diff (HLD §Output: diff)."""

    v_old_label: str
    v_old_class: SchemaClass
    v_old_n_individuals: int
    v_new_label: str
    v_new_class: SchemaClass
    v_new_n_individuals: int
    shared_individuals: int

    added: list[DiffEvent] = field(default_factory=list)
    removed: list[DiffEvent] = field(default_factory=list)
    genetic_id_renamed: list[DiffEvent] = field(default_factory=list)
    master_id_renamed: list[DiffEvent] = field(default_factory=list)
    group_changed_by_class: dict[GroupChangeClass, list[DiffEvent]] = field(
        default_factory=lambda: {c: [] for c in GroupChangeClass}
    )
    gates: dict[str, bool | str] = field(default_factory=dict)

    @property
    def removal_rate(self) -> float:
        if self.v_old_n_individuals == 0:
            return 0.0
        return len(self.removed) / self.v_old_n_individuals

    def summary_line(self) -> str:
        """One-line human-readable summary."""
        substantive = len(self.group_changed_by_class.get(GroupChangeClass.SUBSTANTIVE_REGROUP, []))
        return (
            f"{self.v_old_label} -> {self.v_new_label}: "
            f"{len(self.added)} added, "
            f"{len(self.removed)} removed ({100 * self.removal_rate:.1f}% of {self.v_old_label}); "
            f"{len(self.genetic_id_renamed)} GID renames; "
            f"{len(self.master_id_renamed)} MID renames; "
            f"{substantive} substantive Group ID regroupings."
        )

    def to_dict(
        self,
        *,
        include_class: set[GroupChangeClass] | None = None,
        all_events: bool = False,
    ) -> dict[str, Any]:
        """JSON-serializable summary view.

        Default behavior: `substantive_regroup` events are always
        populated (small list); convention-restructure classes report
        counts only (HLD §Output: diff). The `--include-class` flag opts
        a specific GroupChangeClass's events back in; `all_events=True`
        opts in every group-change class.

        Independently, `added`, `removed`, and `genetic_id_renamed`
        always emit `count` + `rate`; their per-event arrays appear under
        the same parent with key `events` when `all_events=True` OR when
        the corresponding class is in `include_class` (mapped via the
        Literal class strings 'added' / 'removed' / 'genetic_id_renamed').

        Per-class events under `group_changed.events_<class>` keyed by
        the GroupChangeClass enum value (e.g.,
        `events_convention_restructure_suffix`)."""
        include_class = include_class or set()
        if all_events:
            include_class = set(GroupChangeClass)
        # SUBSTANTIVE_REGROUP is always included.
        include_class.add(GroupChangeClass.SUBSTANTIVE_REGROUP)

        added_block: dict[str, Any] = {
            "count": len(self.added),
            "rate": self._added_rate(),
        }
        removed_block: dict[str, Any] = {
            "count": len(self.removed),
            "rate": self.removal_rate,
        }
        gid_renamed_block: dict[str, Any] = {"count": len(self.genetic_id_renamed)}
        if all_events:
            added_block["events"] = [
                {
                    "individual_id": e.individual_id_canonical,
                    "first_seen_genetic_id": e.details.get("first_seen_genetic_id"),
                }
                for e in self.added
            ]
            removed_block["events"] = [
                {
                    "individual_id": e.individual_id_canonical,
                    "last_seen_genetic_id": e.details.get("last_seen_genetic_id"),
                }
                for e in self.removed
            ]
            gid_renamed_block["events"] = [
                {
                    "individual_id": e.individual_id_canonical,
                    "v_old_gids": e.details.get("v_old_gids"),
                    "v_new_gids": e.details.get("v_new_gids"),
                }
                for e in self.genetic_id_renamed
            ]

        group_changed_block: dict[str, Any] = {
            "count": sum(len(v) for v in self.group_changed_by_class.values()),
            "by_class": {
                cls.value: len(self.group_changed_by_class.get(cls, [])) for cls in GroupChangeClass
            },
        }
        for cls in GroupChangeClass:
            if cls not in include_class:
                continue
            group_changed_block[f"events_{cls.value}"] = [
                {
                    "individual_id": e.individual_id_canonical,
                    "group_v_old": e.details.get("group_v_old"),
                    "group_v_new": e.details.get("group_v_new"),
                }
                for e in self.group_changed_by_class.get(cls, [])
            ]

        return {
            "v_old": self.v_old_label,
            "v_old_class": self.v_old_class.value,
            "v_old_n_individuals": self.v_old_n_individuals,
            "v_new": self.v_new_label,
            "v_new_class": self.v_new_class.value,
            "v_new_n_individuals": self.v_new_n_individuals,
            "shared_individuals": self.shared_individuals,
            "added": added_block,
            "removed": removed_block,
            "genetic_id_renamed": gid_renamed_block,
            "master_id_renamed": {
                "count": len(self.master_id_renamed),
                "events": [
                    {
                        "v_old_mid": e.details.get("v_old_mid"),
                        "v_new_mid": e.details.get("v_new_mid"),
                        "via_genetic_id": e.details.get("via_genetic_id"),
                    }
                    for e in self.master_id_renamed
                ],
            },
            "group_changed": group_changed_block,
            "gates": dict(self.gates),
            "summary": self.summary_line(),
        }

    def predict_json_size_bytes(
        self,
        *,
        include_class: set[GroupChangeClass] | None = None,
        all_events: bool = False,
    ) -> int:
        """Predict JSON-serialized size for the configured event arrays.

        Used by the --all-events size-warning path. ~150 bytes per event
        is a calibrated approximation; the full dict has fixed overhead
        on top. Conservative — slightly overestimates so a warning fires
        before the actual write. Pinned in LLD §3.14."""
        include_class = include_class or set()
        if all_events:
            include_class = set(GroupChangeClass)
        include_class.add(GroupChangeClass.SUBSTANTIVE_REGROUP)

        n_events = sum(len(self.group_changed_by_class.get(cls, [])) for cls in include_class)
        if all_events:
            n_events += len(self.added) + len(self.removed) + len(self.genetic_id_renamed)
        # ~150 bytes per event + 2KB fixed overhead.
        return n_events * 150 + 2048

    def _added_rate(self) -> float:
        if self.v_new_n_individuals == 0:
            return 0.0
        return len(self.added) / self.v_new_n_individuals


# === Day-6: library-token + cohort manifest types ===


@dataclass(frozen=True, slots=True)
class LibraryToken:
    """One library's identity across versions. Per LLD §2.6.

    `token` is the most-recent-version's full GID for the chain (post-v0.5
    HLD pin: full GID not stem, since stems collide between suffix classes).
    For Loschbour's snpAD library (dropped before v66), token =
    'Loschbour_snpAD.DG' (the latest version where it appears)."""

    token: str
    per_version_gid: dict[str, str | None]  # version_label -> GID or None
    chain_status: Literal["chained", "orphan", "ambiguous"] = "orphan"


@dataclass(frozen=True, slots=True)
class LibraryIdentityResult:
    """All library tokens for one individual_id_canonical."""

    individual_id_canonical: str
    libraries: tuple[LibraryToken, ...]
    has_ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class ManifestRow:
    """One row in the cohort manifest: one library of one individual.

    Per LLD §2.8. Per-version fields are dicts keyed by version_label rather
    than separate fields, so the dataclass shape stays stable as the user
    supplies different version sets across invocations.

    `per_pair_group_change_class` carries the LLD §4.1 step-11d per-adjacent-
    version-pair `group_id_change_class` value for this individual. Keys are
    `(v_old, v_new)` tuples for each consecutive pair in versions_supplied;
    values are one of the six GroupChangeClass values, the string 'none' for
    unchanged group_ids, or None when the individual is absent from either
    side of the pair."""

    cohort_label: str
    cohort_label_source: str  # 'direct' or 'inferred_from_v44_3' etc.
    individual_id_canonical: str
    library_token: str
    per_version_gid: dict[str, str | None]
    per_version_group_id: dict[str, str | None]
    per_version_snps_hit_1240k: dict[str, int | None]
    persistent_genetic_id: int | None  # latest E-class PGID; None for non-E libraries
    status: str
    per_pair_group_change_class: dict[tuple[str, str], str | None] = field(default_factory=dict)


@dataclass
class CohortManifest:
    """All rows + run metadata for a cohort run."""

    versions_supplied: tuple[str, ...]
    rows: tuple[ManifestRow, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_individuals(self) -> int:
        return len({r.individual_id_canonical for r in self.rows})

    @property
    def n_libraries(self) -> int:
        return len(self.rows)


# === v0.2: run-summary types for the stdout summary block + --report-json ===


@dataclass(frozen=True, slots=True)
class AnnoFileInfo:
    """One row of the 'Loaded N .anno files' header block.

    Captures what the user would have seen at file-load time: the version
    label, source path, row + column counts, and detected schema class."""

    version_label: str
    path: Path
    n_rows: int
    n_cols: int
    schema_class: SchemaClass


@dataclass(frozen=True, slots=True)
class CohortRunSummary:
    """Run-level metadata for the cohort stdout summary block + the v0.2
    A2 `--report-json` sidecar.

    Built by the cohort_cmd orchestrator after the manifest is on disk;
    rendered by `reporting.format_stdout_summary`."""

    versions_supplied: tuple[str, ...]
    anno_file_info: tuple[AnnoFileInfo, ...]
    bridge_auto_count: int
    bridge_manual_count: int
    bridge_collisions: tuple[str, ...]
    cohort_input_path: Path | None
    cohort_input_n_individuals: int
    n_resolved_in_latest: int
    n_added_after_earliest: int
    n_removed_before_latest: int
    group_change_by_class: dict[str, int]
    out_path: Path
    n_rows_written: int
    n_cols_written: int
    turnover_state: str  # 'pass' / 'warn' / 'fail' / 'n/a'
    turnover_rate: float
    elapsed_seconds: float
    # v0.2 A2 (--report-json) fields. Populated by build_cohort_run_summary;
    # rendered by reporting.write_report_json_summary into the LLD-pinned
    # JSON shape.
    n_individuals: int = 0
    label_source_histogram: dict[str, int] = field(default_factory=dict)
    status_histogram: dict[str, int] = field(default_factory=dict)
    cohort_coverage_state: str = "n/a"  # 'pass' / 'warn' / 'fail' / 'n/a'
    cohort_coverage_rate: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiffRunSummary:
    """Run-level metadata for the diff stdout summary block + the v0.2
    A2 `--report-json` sidecar.

    Built by the diff_cmd orchestrator after the diff result is on disk;
    rendered by `reporting.format_stdout_summary` (dispatches on type)."""

    versions_supplied: tuple[str, ...]
    anno_file_info: tuple[AnnoFileInfo, ...]
    bridge_auto_count: int
    bridge_manual_count: int
    bridge_collisions: tuple[str, ...]
    n_added: int
    n_removed: int
    n_genetic_id_renamed: int
    n_master_id_renamed: int
    group_change_by_class: dict[str, int]
    out_path: Path | None  # None when emitting to stdout
    output_mode: str  # 'json' | 'tsv'
    turnover_state: str  # 'pass' / 'warn' / 'fail'
    turnover_rate: float
    substantive_regroup_state: str  # 'pass' / 'fail' / 'n/a' (n/a when threshold=None)
    elapsed_seconds: float
    # v0.2 A2 (--report-json) fields.
    substantive_regroup_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    config: dict[str, Any] = field(default_factory=dict)


# === Day-3: lookup result types ===


@dataclass(frozen=True, slots=True)
class LookupRowRecord:
    """One row's data in a lookup result. Per LLD §2.10."""

    version_label: str
    genetic_id: str
    group_id: str
    snps_hit_1240k: int | None
    persistent_genetic_id: int | None  # class E only; None for A–D


@dataclass
class LookupResult:
    """Output of `aadr-resolve lookup`. Per LLD §2.10 / HLD §Output: lookup."""

    query: str
    individual_id_canonical: str  # equals query if no bridge; query's canonical post-Day-4
    matched_via: Literal["individual_id", "genetic_id", "not_found"]
    # Day-4 will populate this from MID-bridge events; Day-3 leaves empty.
    master_id_bridge: list[dict[str, str]] = field(default_factory=list)
    per_version: dict[str, list[LookupRowRecord]] = field(default_factory=dict)
    status_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view of the result."""
        return {
            "query": self.query,
            "individual_id_canonical": self.individual_id_canonical,
            "matched_via": self.matched_via,
            "master_id_bridge": list(self.master_id_bridge),
            "per_version": {
                v: [
                    {
                        "version_label": r.version_label,
                        "genetic_id": r.genetic_id,
                        "group_id": r.group_id,
                        "snps_hit_1240k": r.snps_hit_1240k,
                        "persistent_genetic_id": r.persistent_genetic_id,
                    }
                    for r in rows
                ]
                for v, rows in self.per_version.items()
            },
            "status_flags": list(self.status_flags),
        }

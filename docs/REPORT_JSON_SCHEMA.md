# --report-json schema

Run-level JSON sidecar emitted by `aadr-resolve cohort --report-json PATH`
and `aadr-resolve diff --report-json PATH`. Loads cheaply via `json.load`
regardless of corpus size (~few KB). Intended consumers: CI dashboards,
sibling tools (ancestry-pipeline-tool), and machine-readable gate echoes.

Written BEFORE any `ValidationError` raise — when a gate fires and the
process exits 1, the sidecar is still on disk so the failure shape can
be inspected.

## Common top-level keys (both cohort and diff)

| Key                  | Type                          | Description                                                       |
|----------------------|-------------------------------|-------------------------------------------------------------------|
| `versions_supplied`  | `list[str]`                   | Version labels in user-supplied order.                            |
| `schemas_detected`   | `dict[str, str]`              | Version label → schema class letter (`"A"`-`"E"`).               |
| `bridge`             | `dict`                        | See below.                                                        |
| `gates`              | `dict[str, str \| float]`     | State per gate (`"pass"` / `"warn"` / `"fail"` / `"n/a"`) + rate. |
| `warnings`           | `list[str]`                   | Stderr-emitted warnings from this run.                            |
| `config`             | `dict`                        | CLI-resolved flag values (thresholds + behavior toggles).         |
| `elapsed_seconds`    | `float`                       | Wallclock from orchestrator entry to summary build.              |

### `bridge` sub-block

```json
{
  "auto_count": 9,
  "manual_count": 0,
  "collisions": []
}
```

- `auto_count`: GID-stable MID-rename events detected automatically.
- `manual_count`: rows from `--mid-bridge FILE` overlay.
- `collisions`: cross-lab MID collision descriptions when
  `--on-mid-collision warn`.

## Cohort variant — `cohort` block

Present only when emitted by `aadr-resolve cohort`.

```json
{
  "cohort": {
    "n_individuals": 40,
    "n_libraries": 47,
    "n_resolved_in_latest": 37,
    "n_added_after_earliest": 1,
    "n_removed_before_latest": 2,
    "label_source_histogram": {
      "direct": 37,
      "inferred_from_v_v44_3": 3
    },
    "status_histogram": {
      "present_all": 35,
      "removed_before_v66_0": 2,
      "added_after_v44_3": 1
    },
    "group_change_by_class": {
      "convention_restructure_suffix": 18,
      "convention_restructure_country": 0,
      "convention_restructure_order": 0,
      "convention_restructure_punct": 0,
      "partial": 1,
      "substantive_regroup": 2
    }
  }
}
```

`gates` for cohort:

```json
{
  "gates": {
    "turnover": "pass",
    "turnover_rate": 0.05,
    "cohort_coverage": "pass",
    "cohort_coverage_rate": 0.925
  }
}
```

## Diff variant — `diff` block

Present only when emitted by `aadr-resolve diff`.

```json
{
  "diff": {
    "added": 1247,
    "removed": 3341,
    "genetic_id_renamed": 18,
    "master_id_renamed": 9,
    "group_change_by_class": {
      "convention_restructure_suffix": 4128,
      "convention_restructure_country": 12,
      "convention_restructure_order": 31,
      "convention_restructure_punct": 87,
      "partial": 145,
      "substantive_regroup": 38
    }
  }
}
```

`gates` for diff:

```json
{
  "gates": {
    "turnover": "pass",
    "turnover_rate": 0.169,
    "substantive_regroup": "pass",
    "substantive_regroup_count": 38
  }
}
```

The `substantive_regroup` state is `"n/a"` when
`--substantive-regroup-fail` is not set (the default — gate disabled).
When set, state is `"pass"` unless the count exceeds the threshold,
then `"fail"`.

## `config` block

Echoes the CLI-resolved flag values from the run. Useful for verifying
which threshold the run used. Keys depend on the subcommand.

### Cohort
```json
{
  "config": {
    "turnover_warn": 0.05,
    "turnover_fail": 0.30,
    "cohort_coverage_warn": 0.50,
    "cohort_coverage_fail": 0.25,
    "no_propagate": false,
    "collapse_to_individual": false,
    "gid_preference": ["AG", "DG", "SG", "HO", "TW", "BY", "AA", "EC", "WGC", "bare"],
    "output_format": "tsv"
  }
}
```

### Diff
```json
{
  "config": {
    "turnover_warn": 0.05,
    "turnover_fail": 0.30,
    "substantive_regroup_fail": null,
    "include_classes": [],
    "all_events": false,
    "output_format": "json"
  }
}
```

## Stability

The schema is versioned implicitly with `aadr-resolve` itself. Breaking
changes follow semver:
- Adding a new top-level key: **MINOR** bump (consumers shouldn't fail
  on unknown keys).
- Removing or renaming a key: **MAJOR** bump.
- Changing the meaning of a value (e.g., re-defining `"pass"`): **MAJOR**.

Sub-block fields (`bridge.auto_count`, `cohort.n_individuals`, etc.)
follow the same convention.

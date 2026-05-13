# aadr-resolve

AADR cross-version GeneticID / MasterID join utility for ancient-DNA and
population-genetics workflows.

`aadr-resolve` reads AADR (Allen Ancient DNA Resource) `.anno` files
across one or more releases and resolves the cross-version sample-ID
join through the Master ID column — the part every ancient-DNA pipeline
currently re-implements with custom awk. It handles AADR's progressive
de-anonymization (`I0001` in v44.3 → `Loschbour.AG` in v66) and the
periodic Master-ID renames (9-18 per consecutive version pair; ~62
cumulative v44.3 → v66.0) automatically.

Designed and bench-verified against AADR releases v44.3, v50.0, v52.2,
v54.1, v62.0, and v66.0; five schema classes (A–E) are recognized
out-of-the-box via in-package YAML header signatures.

## Install

```bash
pip install aadr-resolve
```

Requires Python 3.11+. Dependencies: pandas 2.x, click 8.x, PyYAML 6.x.

## Quickstart

**Resolve a single sample across two AADR releases.**

```bash
aadr-resolve lookup I0001 \
    --anno-files v44.3_1240K_public.anno \
    --anno-files v66.0_1240K_public.anno
```

Output (stdout):

```
query: I0001
canonical individual_id: Loschbour    (matched via individual_id)
v44.3 rows: 1
  I0001  Luxembourg_Loschbour  537,182 SNPs
v66.0 rows: 2
  Loschbour.AG  Luxembourg_Mesolithic.AG  155,036 SNPs  pgid=33
  Loschbour.DG  Luxembourg_Mesolithic.DG  620,881 SNPs  pgid=39136
master_id_bridge: v44.3 I0001 → v66.0 Loschbour (via shared GID Loschbour.DG)
status: present_in_2_of_2_versions; multi_row; individual_id_renamed
```

**Recreate a cohort against a newer release.**

```bash
aadr-resolve cohort patterson_2022_whga.txt \
    --anno-files v44.3_1240K_public.anno \
    --anno-files v66.0_1240K_public.anno \
    --cohort-version v44.3 \
    -o whga_v66_manifest.tsv
```

The manifest is a TSV with one row per (individual × library), with
per-version `genetic_id` / `group_id` / `snps_hit_1240k` columns plus
per-adjacent-pair `group_id_change_class_v{old}_to_v{new}` columns,
ready to feed into downstream relabeling tools like `pgen-samplebind`.

Output (stdout summary block):

```
Loaded 2 .anno file(s):
  [v44.3] v44.3_1240K_public.anno: 9,275 rows × 43 cols, class A
  [v66.0] v66.0_1240K_public.anno: 23,250 rows × 49 cols, class E

Cross-version bridge:
  GID-stable MID-rename detection:  9 events
  Manual --mid-bridge entries:      0
  Cross-lab MID collision check:    no collisions detected

Cohort input: patterson_2022_whga.txt (40 individuals)
  Resolved in latest version:  37
  Added after earliest:        1
  Removed before latest:       2

Group ID changes (v44.3 → v66.0):
  convention_restructure_suffix      18
  partial                             1
  substantive_regroup                 2

Wrote whga_v66_manifest.tsv (40 rows × 13 cols)
Sample turnover within cohort: 5.0% — PASS

Done in 1.4s.
```

Add `--quiet` to suppress the block. Add `--report-json summary.json`
to also emit a run-level JSON sidecar that loads cheaply via
`json.load` — see [docs/REPORT_JSON_SCHEMA.md](docs/REPORT_JSON_SCHEMA.md).

**Structured diff between two releases.**

```bash
aadr-resolve diff v62.0_1240K_public.anno v66.0_1240K_public.anno \
    --tsv > v62_to_v66_changes.tsv
```

Emits one row per change event: added, removed, genetic_id_renamed,
master_id_renamed, group_changed (with a per-class label —
`convention_restructure_suffix` etc.).

For large diffs at AADR scale, stream the per-event TSV alongside a
small summary JSON instead of buffering the full event list:

```bash
aadr-resolve diff v62.0_1240K_public.anno v66.0_1240K_public.anno \
    -o changes_summary.json \
    --report changes_events.tsv \
    --report-json summary.json
```

`--report PATH` streams one row per event (constant memory) and
`--report-json PATH` writes the run-level summary (~few KB, loads
cheaply via `json.load`). The diff stdout summary block routes to
stderr when stdout is carrying the JSON payload, so pipes stay clean.

## Subcommands

| Command  | Purpose                                                     |
|----------|-------------------------------------------------------------|
| `lookup` | Resolve a single sample across N versions                    |
| `cohort` | Emit a cross-version manifest for a user-supplied cohort     |
| `diff`   | Structured diff between two versions                         |
| `join`   | Wide-format pairwise table over the full intersection        |
| `schema` | Diagnostic: report the detected schema class                 |

### `aadr-resolve lookup`

```
aadr-resolve lookup INDIVIDUAL_OR_GENETIC_ID \
    --anno-files PATH [--anno-files PATH ...]
    [--json]
```

Treated as `individual_id` by default; falls back to `genetic_id` if no
IID matches. The MID-rename bridge is built automatically from the
supplied versions and reported under `master_id_bridge` in the output.

### `aadr-resolve cohort`

```
aadr-resolve cohort COHORT_FILE \
    --anno-files PATH [--anno-files PATH ...]
    [--cohort-version LABEL]
    -o OUT.tsv [--json]
    [--no-propagate]
    [--collapse-to-individual]
    [--gid-preference AG,DG,SG,HO,TW,BY,AA,EC,WGC,bare]
    [--turnover-warn 0.05] [--turnover-fail 0.30]
    [--cohort-coverage-warn 0.50] [--cohort-coverage-fail 0.25]
    [--report-json PATH]
```

`COHORT_FILE` is a TSV: one column for `individual_id`, optional second
column for `cohort_label`. `--cohort-version` is auto-detected from the
supplied annos when omitted. Default output is row-per-(individual ×
library); `--collapse-to-individual` reduces to one row per individual
via the `--gid-preference` suffix priority. `--report-json PATH` writes
a run-level summary sidecar (~few KB) for CI dashboards.

### `aadr-resolve diff`

```
aadr-resolve diff V_OLD.anno V_NEW.anno
    [--json | --tsv]
    [-o OUT]
    [--include-class CLASS [--include-class CLASS ...]]
    [--all-events]
    [--turnover-warn 0.05] [--turnover-fail 0.30]
    [--substantive-regroup-fail INT]
    [--report PATH] [--report-json PATH]
```

JSON output is summary-first: per-class counts always included;
per-event arrays only for `substantive_regroup` (always) and any class
named via `--include-class`, or all classes when `--all-events` is set.
`--tsv` switches to streamed one-row-per-event format.

For large diffs, prefer the streamed sidecars: `--report PATH` writes
per-event TSV with constant memory; `--report-json PATH` writes the
run-level summary. The summary block routes to stderr when stdout is
the JSON payload, so `aadr-resolve diff a.anno b.anno | jq ...` works
without breaking the pipe.

### `aadr-resolve join`

```
aadr-resolve join V_OLD.anno V_NEW.anno
    -o OUT.tsv [--json]
    [--collapse-to-individual]
    [--gid-preference AG,DG,SG,HO,TW,BY,AA,EC,WGC,bare]
```

Wide-format pairwise table over the full v_old ∪ v_new canonical
individual_id set. Same output schema as `cohort`; useful when you
don't have a pre-existing cohort list.

### `aadr-resolve schema`

```
aadr-resolve schema PATH [--json]
```

Diagnostic: detects which schema class (A–E) the `.anno` belongs to,
reports the column layout. Useful for debugging "why does this `.anno`
not load."

## Shared options

These apply to all subcommands:

| Option                       | Default | Notes                                                          |
|------------------------------|---------|----------------------------------------------------------------|
| `--schema-override CLASS`    | auto    | Force schema class A/B/C/D/E (e.g., renamed `.anno`)          |
| `--version-label LABEL`      | auto    | Force version label (when filename pattern doesn't match)     |
| `--mid-bridge FILE`          | none    | Manual master_id-rename TSV layered on auto-detected bridge   |
| `--on-mid-collision {error,warn}` | error | Cross-lab MID collision policy                                |
| `--quiet`                    | false   | Suppress the stdout summary block (cohort + diff + join write phase) |

## Library API

The same functionality is available in-process:

```python
from aadr_resolve import (
    AnnoFrame,
    resolve_master_ids,
    resolve_genetic_ids,
)

# Resolve v44.3 Master IDs to v66.0 GeneticIDs
result = resolve_master_ids(
    ["I0001", "Bichon", "Mota"],
    src_version="v44.3",
    dst_version="v66.0",
    anno_paths={
        "v44.3": "v44.3_1240K_public.anno",
        "v66.0": "v66.0_1240K_public.anno",
    },
    mid_bridge="manual_renames.tsv",  # optional override TSV
)
# result = {"I0001": "Loschbour.AG", "Bichon": "Bichon.SG", "Mota": None}
```

`resolve_genetic_ids` does the GID → GID inverse:

```python
result = resolve_genetic_ids(
    ["I0001"],
    src_version="v44.3",
    dst_version="v66.0",
    anno_paths={...},
)
# result = {"I0001": ["Loschbour.AG", "Loschbour.DG"]}  # multi-row IID
```

Both functions also accept `anno_frames={label: AnnoFrame, ...}` as an
alternative to `anno_paths={label: path, ...}` — useful when you've
already loaded the frames once and want to reuse them across calls.

Direct `AnnoFrame` access for lower-level work:

```python
from aadr_resolve import AnnoFrame

af = AnnoFrame.from_path("v66.0_1240K_public.anno", version_label="v66.0")
af.schema_class       # SchemaClass.E
af.individual_id      # pd.Series of canonical IIDs
af.genetic_id         # pd.Series
af.persistent_genetic_id  # pd.Series of Int64 nullable (E only; all-NaN elsewhere)
af.date_calbp         # pd.Series of Int64 nullable
af.coverage           # pd.Series of Float64 nullable
af.path               # original Path, useful for re-creating anno_paths dicts
```

### Exception hierarchy

All errors derive from `aadr_resolve.AadrResolveError`. Sibling tools
catching aadr-resolve errors can `except aadr_resolve.<Class>`:

| Class                       | Maps to exit | Trigger                                              |
|-----------------------------|--------------|------------------------------------------------------|
| `ValidationError`           | 1            | Turnover gate, coverage gate, substantive-regroup gate |
| `IOFailure`                 | 2            | File not found, lock held, malformed TSV             |
| `InvariantViolation`        | 3            | Schema YAML malformed (rare)                          |
| `SchemaDetectionError`      | 3            | Header signature unknown                              |
| `MissingNativeFieldError`   | 3            | Canonical field requested for a class that lacks it  |
| `CollisionDetected`         | 3            | Cross-lab MID collision under `error` policy         |
| `UsageError`                | 4            | Bad CLI args; cohort file has no matching version    |

## Exit codes

Stable across versions. CI workflows can grep:

- `0` — success
- `1` — soft-validation failure (any of the gates)
- `2` — I/O failure
- `3` — invariant violation (schema, MID collision)
- `4` — usage error (bad CLI args)

## Troubleshooting

**"unknown .anno schema signature"** — your `.anno` header doesn't
match any of the 5 known classes. Either the file is from a newer AADR
release (file an issue with the bench-verify diff), or the file has
been edited. Workarounds:

- `--schema-override A|B|C|D|E` forces a class without signature check.
- `--version-label vN.N` forces a version label when the filename
  doesn't match a known pattern.

**"cross-lab MID collision"** — the GID-stability check found a Master
ID that maps to two different individuals in different versions.
This indicates either a real data error in AADR or a cross-lab naming
collision (rare). Workarounds:

- `--on-mid-collision warn` continues with a stderr warning and marks
  affected rows with `library_chain_ambiguous` status.
- `--mid-bridge FILE` lets you specify the correct mapping manually.

**"sample turnover gate (fail)"** — removal rate exceeded the
`--turnover-fail` threshold (default 30%). Indicates either a major
AADR cleanup (the v62→v66 bump removed ~17%) or that the wrong files
are being compared. Override with `--turnover-fail 1.0` to disable.

**"cohort coverage gate (fail)"** — fewer than 25% of cohort entries
resolved in the supplied versions. Usually means the cohort file uses
IDs from a version not in the supplied set. Check `--cohort-version`.

**"substantive_regroup gate (fail)"** — only fires on `diff` when
`--substantive-regroup-fail INT` is set and the count of
`substantive_regroup` events exceeds the threshold. Inspect the events
via `--report PATH` or `--include-class substantive_regroup` before
adjusting the threshold.

**Pandas ParserError on a v52 / v54 `.anno`** — these versions contain
embedded quote characters in some `full_date` cells. aadr-resolve reads
with `csv.QUOTE_NONE` to side-step pandas's default quote-handling;
upgrade if you're on an older version.

## Composition with the broader ecosystem

The manifest TSV is the handoff format to downstream relabeling tools.
Typical pipeline pairing aadr-resolve's join output with a PLINK2
sample-rename step:

```bash
aadr-resolve cohort patterson_2022.txt \
    --anno-files v44.3_1240K_public.anno \
    --anno-files v66.0_1240K_public.anno \
    -o cohort_manifest.tsv
pgen-samplebind merge \
    --relabel-from cohort_manifest.tsv \
    --output merged_v66.pgen \
    v44.3.pgen v66.0.pgen
```

## Cohort manifest column layout

Stable column order across versions:

1. `cohort_label`, `cohort_label_source`, `individual_id_canonical`,
   `library_token`
2. Per-version triples in user-supplied order:
   `v{X}_genetic_id`, `v{X}_group_id`, `v{X}_snps_hit_1240k`
3. Per-adjacent-pair classifier columns:
   `group_id_change_class_v_{old}_to_v_{new}` (one per consecutive pair;
   values: `convention_restructure_{suffix,country,order,punct}`,
   `partial`, `substantive_regroup`, `none`, or `--` if absent)
4. `persistent_genetic_id` (only when the latest version is class E and
   at least one row has a value)
5. `status`

Missing-cell sentinel is `--` for every column (both string fields and
nullable-Int64 numerics).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local development setup,
the test layout (default / slow / external / perf markers), lint and
type-check invocations, and the release process.

## License

MIT.

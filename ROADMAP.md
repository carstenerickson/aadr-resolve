# Roadmap

Deferred work, candidates for future releases. This list is
opinionated and reflects the maintainer's current judgment — file an
issue at <https://github.com/carstenerickson/aadr-resolve/issues> if
real-world use surfaces a need to reprioritize.

For architectural context on any of these, see
[DEVELOPMENT.md](DEVELOPMENT.md).

## Status (as of v0.2.0)

The v0.2 release closed the reporting layer (stdout summary block,
`--report PATH`, `--report-json PATH`, per-pair `group_id_change_class`
columns, CI coverage gate). The items below are explicitly *out of
scope* for v0.2; some are candidates for v0.3 if dogfood demands.

## Larger items

### Concurrency contract

`src/aadr_resolve/concurrency.py` does not exist. No subcommand
acquires an `fcntl.flock` on the output path. Two parallel
invocations of `aadr-resolve cohort -o foo.tsv …` will clobber each
other silently.

**Shape if added:** advisory `fcntl.flock` (or `msvcrt.locking` on
Windows, though Windows isn't a supported platform) on the output
file, acquired before the orchestrator's write phase. `IOFailure`
(exit 2) on lock held. The orchestrator sequence is already
"write → evaluate gates → summarize → raise"; the lock would wrap
the whole sequence so a gate failure still releases.

**Why deferred:** no observed real-world contention. The likely
trigger is CI parallelism in a multi-cohort batch script.

### Schema-detection low-confidence gate

Current behavior: any header signature not in the registry hard-
fails with `SchemaDetectionError` → exit 3. The intended behavior is
a fuzzy fallback: when `ncols` matches a known class but one of the
two signature columns disagrees, exit 1 (validation) rather than 3
(invariant) with a stderr note naming the likely class.

**Shape if added:** refactor `schema.detect_class` to track partial
matches and emit a `LowConfidenceMatch` result for the orchestrator
to handle. The fuzzy fallback runs only when `--strict-schema` is
off (default off).

**Why deferred:** touches more invariants than fits a polish-day
delta — affects the exit-code matrix, the integration tests for
`schema_cmd`, and the loader's error-message wording.

### `RunPolicy` / `RunContext` orchestration types

Current behavior: each `commands/*_cmd.py` orchestrator marshals
click options inline via `ctx.obj["shared_opts"]` and forwards them
to the core module as keyword arguments. The original design had a
typed `RunPolicy` dataclass capturing the resolved policy
(thresholds, gate flags, output paths) and a `RunContext` capturing
the runtime state (AnnoFrames, bridge, start time).

v0.2 added per-orchestrator `CohortRunSummary` / `DiffRunSummary` on
the *output* side. The *input* side stays inline. Functionally
equivalent; structurally less testable.

**Why deferred:** purely a refactor; no behavior change. Pick up
when the next subcommand or shared flag makes the marshalling
verbose enough to feel painful.

### Rule C transitive bridge

`library_token` currently applies Trivial + Rule A + Rule B (see
[DEVELOPMENT.md §1](DEVELOPMENT.md#library-token-chain-rules)). For
a version gap where only v44.3 and v66.0 are supplied (skipping
v62.0), neither rule fires for the `I0001 ↔ Loschbour.AG` chain:

- Rule A needs the bare-numeric stem (`I0001`) and a suffixed GID
  with the same stem (`I0001.AG`) in *adjacent* versions. With
  v62.0 dropped, `I0001` (v44.3) sees only `Loschbour.AG` (v66.0)
  next, and the stems disagree.
- Rule B needs single-library-per-suffix-class in both sides with
  differing stems. v44.3 has zero `.AG` rows for the chain.

**Shape if added:** Rule C composes the MID-rename bridge with
Rule A — `bridge.canonical_id(v44.3, 'I0001') == 'Loschbour'`, so
the union-find should add an edge `(v44.3, I0001) — (v66.0, Loschbour.AG)`
when the MID-rename event maps the stem.

**Why deferred:** the v0.2 algorithm correctly emits orphan rows in
this scenario; users add the missing intermediate version
(`--anno-files v62.0_…`) and the chain closes. Rule C is the
"single-anno-step" convenience.

## Smaller items

### N-version `join` wide-form

`join` is currently pairwise (`V_OLD.anno`, `V_NEW.anno`). 3+
version outer joins are well-defined for inner intersections but
get awkward for cohort_label propagation choice (which version
"wins"). `cohort` already supports N versions; revisit if dogfood
demands.

### Coverage CLI exposure

`AnnoFrame.coverage_via(field)` is library-only. No CLI flag exposes
the derived-proxy path for class D's `snps_hit_1240k`. Sibling tools
like `aadr-subset` carry `--coverage-column NAME`; the
aadr-resolve-side equivalent would be `--coverage-source {native, snps_hit_1240k}`
on the cohort/join subcommands.

### `--missing-sentinel STRING` CLI flag

The TSV missing-cell sentinel is hardcoded to `--` in
`reporting.TSV_NULL_SENTINEL`. Bench-verify never observed a real
`--` cell in AADR data. The loader emits a stderr warning if it
sees one. If a real collision surfaces, add a CLI override.

### `.snp` cross-version diff

v0.2 is `.anno`-only. AADR's `.snp` files also drift across
releases; a sibling `aadr-resolve snp diff` would fit. Out of scope
unless requested.

### Out of scope (not deferred — explicitly out of scope)

- Population-label normalization (the `group_id` classifier handles
  syntactic restructure; full ontology mapping is a different tool)
- Free-text "Full Date" parsing (canonical date_calbp is already
  bench-verified clean across all 6 versions)
- HumanOrigins-specific comparison (different SNP panel; out of
  scope for the cross-version join utility)

## Drift between spec and code

Noteworthy but not bugs:

- Test layout uses behavior-numbered integration files
  (`tests/integration/test_hld_*`) rather than the per-module
  unit-test files originally specified.
- The `schema-sync-check` CI job and `scripts/sync_schemas.sh` from
  the spec don't exist; schemas live only at
  `src/aadr_resolve/schemas/`.
- The publish workflow filename is
  `.github/workflows/publish.yml`; the spec named it `release.yml`.

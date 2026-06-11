# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Class C now loads the published v54.1 `.anno` files.** The released v54.1
  1240K and HO annotations carry a trailing tab, so the loader drops the phantom
  column and detection sees 35 columns — but class C accepted only 36, so real
  public files failed with `SchemaDetectionError`. The synthetic class-C fixture
  filled its trailing column and never exercised the drop, hiding the gap. Class C
  now accepts `n_columns: [35, 36]`; the trailing column is unmapped, so every
  field stays at the same position. Added a regression test for the 35-column
  trailing-tab shape.

## [0.2.0] — 2026-05-12

Reporting layer + polish. Closes the biggest doc/code gap in v0.1 by
delivering the stdout summary block, the per-event TSV streaming
sidecar, and the run-level JSON summary sidecar. Plus the cohort
manifest per-adjacent-pair `group_id_change_class` columns, a CI
coverage gate, and the GitHub Actions Node.js 24 bump.

### Added — reporting layer
- **Stdout summary block** on `cohort` and `diff`. Replaces the v0.1
  "Wrote N rows" placeholder with a rich multi-section block: loaded
  `.anno` files (rows/cols/class), bridge
  counts, cohort resolution histogram OR diff event histogram,
  group_id-change histogram by class, write line, turnover gate
  verdict, elapsed time. `--quiet` suppresses.
- **`--report-json PATH`** on `cohort` and `diff`: run-level JSON
  summary sidecar (~few KB, loads cheaply via `json.load`). Shape
  documented in [docs/REPORT_JSON_SCHEMA.md](docs/REPORT_JSON_SCHEMA.md).
  Includes `versions_supplied`, `schemas_detected`, `bridge` block,
  `cohort` or `diff` block (with histograms), `gates` block (with
  state + rate echoes), `warnings`, `config` (CLI flag values echoed),
  `elapsed_seconds`. Written BEFORE any `ValidationError` raise so CI
  can inspect failure shapes on gate failures.
- **`--report PATH`** on `diff`: streamed per-event TSV sidecar via
  `diff.iter_report_rows` generator → `reporting.write_report_tsv`.
  Constant memory regardless of event count; preferred over
  `--all-events` at AADR scale.

### Added — cohort manifest
- **Per-adjacent-pair `group_id_change_class_v{old}_to_v{new}` columns**
  in the cohort manifest TSV. One column per
  consecutive version pair; values are one of the six
  `GroupChangeClass` values, `'none'` for unchanged group_ids, or `--`
  when the individual is absent from either side of the pair.
- New `n_individuals` + `label_source_histogram` + `status_histogram`
  + `per_pair_group_change_class` fields on `ManifestRow` /
  `CohortRunSummary`; surface via the new JSON sidecar.

### CI + tooling
- **Coverage gate**: `pytest --cov=aadr_resolve --cov-fail-under=85`
  in the default CI matrix job. `.coveragerc` excludes orchestrator
  click wrappers, the entry shim, and the top-level CLI group;
  actual coverage 91.7%.
- **Default `pytest` excludes slow / external / perf markers**.
  Use `pytest -m slow` to opt in.
- **GitHub Actions bump past Node.js 20 deprecation** (June 2026
  cutover): `actions/checkout` v4 → v6, `actions/setup-python` v5 →
  v6, `actions/upload-artifact` v4 → v7, `actions/download-artifact`
  v4 → v8.

### Routing
- The `diff` stdout summary block routes to **stderr** when stdout is
  carrying the JSON/TSV payload (no `-o`), so the pipe stays clean.
  When `-o PATH` is set, the summary goes to stdout like cohort.

## [0.1.0] — 2026-05-12

Initial release. Five subcommands, the library API surface, the
validation gate suite, and a CI matrix.

### Subcommands
- `lookup`: single-sample resolution across N versions with the
  MID-rename bridge automatically applied; individual_id-first matching
  with genetic_id fallback; multi-row-per-IID semantics.
- `cohort`: cross-version manifest from a user-supplied cohort file;
  one row per (individual × library) by default; `--collapse-to-individual`
  for one-row-per-individual; cohort_label propagation across versions
  with `direct` / `inferred_from_vN` provenance.
- `diff`: structured cross-version diff; added / removed /
  genetic_id_renamed / master_id_renamed / group_changed events;
  six-class group_id-change classifier; JSON summary-first by default,
  `--include-class CLASS` and `--all-events` for full event arrays;
  100 MB size-warning on `--all-events`.
- `join`: wide-format pairwise table over the full intersection; shares
  the cohort manifest schema.
- `schema`: diagnostic for inspecting the detected class.

### Data model
- Five bench-verified schema classes A–E (v44.3, v50.0, v52.2, v54.1,
  v62.0, v66.0) with in-package YAML signatures.
- Three-ID model: `genetic_id` (per-row), `individual_id` (per-physical-
  individual; the cross-version join key; renamed from `Master ID` to
  `Individual ID` in v66), `persistent_genetic_id` (E only).
- MID-rename bridge: GID-stable detection across consecutive version
  pairs; `--mid-bridge FILE` manual override; cross-lab collision
  detection.
- Library-token chain: Trivial + Rule A (bare → suffixed promotion) +
  Rule B (single-library-per-suffix-class).
- Six-class Group ID change classifier: convention_restructure_{suffix,
  country, order, punct} + partial + substantive_regroup.

### Validation gates (exit-1)
- Turnover gate: per-version-pair removal rate ≥ `--turnover-fail`
  (default 0.30); `--turnover-warn` (default 0.05) emits stderr warning.
- Substantive-regroup gate: diff-only, opt-in via
  `--substantive-regroup-fail INT`.
- Cohort-coverage gate: resolved fraction < `--cohort-coverage-fail`
  (default 0.25); `--cohort-coverage-warn` (default 0.50) warns.

### Library API
- `AnnoFrame.from_path(...)`: typed pandas-Series accessors for every
  canonical column; nullable Int64 / Float64 dtypes; `.path` round-trip.
- `resolve_master_ids(...)`: list of v_src IIDs → dict of v_dst GIDs.
- `resolve_genetic_ids(...)`: list of v_src GIDs → dict of v_dst GID
  lists (multi-row IID semantics preserved).
- `mid_bridge` kwarg on both for manual MID-rename overrides.
- Full exception hierarchy exposed at the top-level namespace:
  `AadrResolveError`, `ValidationError`, `IOFailure`, `InvariantViolation`,
  `SchemaDetectionError`, `MissingNativeFieldError`, `CollisionDetected`,
  `UsageError`.

### Performance + CI
- Perf benchmark: <2s wallclock single-core M2 for full v44.3 → v66.0
  cohort (40-sample WHGA).
- GitHub Actions matrix: Ubuntu + macOS × Python 3.11/3.12/3.13.
- Standalone perf runner with per-phase timings.

### Tests
- 153 passing tests covering behavioral specification end-to-end
  plus implementation-detail coverage.
- External-gated tests (`@pytest.mark.external`): v62↔v66 diff
  regression, pgen-samplebind handoff, calibration anchor resolution,
  real-AADR perf, and the WHGA self-dogfood pass.

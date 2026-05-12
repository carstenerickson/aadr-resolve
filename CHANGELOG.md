# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-12

Initial release. Implements the full HLD specification end-to-end across
five subcommands, the library API surface, the validation gate suite,
and CI matrix.

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
- HLD test 42 perf benchmark: <2s wallclock single-core M2 for full
  v44.3 → v66.0 cohort (40-sample WHGA).
- GitHub Actions matrix: Ubuntu + macOS × Python 3.11/3.12/3.13.
- Standalone perf runner with per-phase timings.

### Tests
- 153 passing tests covering HLD tests 1-41 (one per HLD-numbered test)
  plus implementation-detail coverage.
- External-gated tests (`@pytest.mark.external`): HLD tests 23
  (v62↔v66 diff regression), 25 (pgen-samplebind handoff), 26
  (calibration anchor resolution), 42 (real-AADR perf), and the
  WHGA self-dogfood pass.

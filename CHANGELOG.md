# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Day 1
- Repo skeleton + pyproject.toml + LICENSE + CHANGELOG.
- `aadr_resolve` package scaffold with 5 core modules wired:
  `types`, `errors`, `schema`, `version_inference`, `loader`, `annoframe`,
  `cli`, `commands/schema_cmd`.
- Schema registry: 5 in-package YAMLs (class A–E) loaded via
  `importlib.resources`; auto-detection from `(ncols, normalize(col[0]),
  normalize(col[1]))` signature; `--schema-override CLASS` override.
- `AnnoFrame` class scaffold: `from_path` constructor with eager
  `pandas.read_csv` pipeline (QUOTE_NONE, header normalization,
  trailing-tab phantom-column dropper); raw-Series typed accessors
  (`.genetic_id`, `.individual_id`, `.persistent_genetic_id`,
  `.group_id`); date and coverage accessors stubbed to
  `NotImplementedError` until Day 2.
- `schema` subcommand end-to-end with both stdout and `--json` outputs.
- Deterministic synth-fixture generator + 5 committed mini-`.anno`
  fixtures (one per class).
- HLD tests 1–5 (schema-discovery) passing.

## [0.1.0] — TBD
Initial release.

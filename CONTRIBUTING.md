# Contributing to aadr-resolve

Thanks for your interest in improving `aadr-resolve`. This document
covers local development setup, the test layout, coding conventions,
how to file issues and PRs, and the release process. For user-facing
usage, see [README.md](README.md).

## Development setup

```bash
git clone https://github.com/carstenerickson/aadr-resolve
cd aadr-resolve
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.11+ (CI matrix: 3.11, 3.12, 3.13).

## Tests

The suite is partitioned by pytest markers so the default invocation
stays fast (~10s). Slow / external / perf tests are opt-in.

```bash
# Default suite (fast; excludes slow / external / perf)
pytest -ra

# Slow tests (synth perf benchmark; ~5 min)
pytest -m slow -ra

# External tests (real AADR files; requires AADR_CACHE env var)
AADR_CACHE=/path/to/cache pytest -m external -ra

# Standalone perf benchmark with per-phase timings
AADR_CACHE=/path/to/cache python -m benchmarks.perf_bench
```

Most contributors only need the default suite. The external suite
requires a local cache of real `.anno` files (set `AADR_CACHE` to a
directory containing the filenames the tests expect, e.g.
`v62.0_1240K_public.anno`); this is normally a maintainer-only setup.

The default-suite mark filter is pinned in `pyproject.toml`
(`[tool.pytest.ini_options].addopts`). The synth perf test runs
noticeably slower under `--cov` instrumentation; keeping it out of the
default coverage-gate run keeps that gate honest.

### Coverage

CI runs the default suite with `--cov=aadr_resolve --cov-fail-under=85`.
Exclusions live in `.coveragerc` — chiefly the CLI orchestrators under
`src/aadr_resolve/commands/`, which are covered end-to-end by the
integration tests rather than line-by-line.

## Lint, format, types

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
```

Ruff config + ignores are in `pyproject.toml`. `mypy` runs in `strict`
mode with `pandas` / `yaml` overrides.

## CI

GitHub Actions runs the default suite across Python 3.11/3.12/3.13 ×
Ubuntu+macOS on every push and PR; see `.github/workflows/ci.yml`.
The `slow` job opts into `-m "slow and not external"`. External tests
are not run in CI (no AADR cache).

## Filing issues and PRs

Open issues and PRs at
<https://github.com/carstenerickson/aadr-resolve/issues>.

- Bug reports: include the AADR version(s) involved, the exact CLI
  invocation, and the full error output. If the issue is a schema
  detection failure, `aadr-resolve schema PATH` output is invaluable.
- Feature PRs: describe the user-facing behavior change in the PR
  body and add at least one integration test under
  `tests/integration/`. Behavior changes that affect exit codes,
  output formats, or default gate thresholds are versioned per
  [semver](https://semver.org/spec/v2.0.0.html) — call those out
  explicitly so the reviewer can route them into the right release.

## Coding conventions

- Type hints required (`mypy --strict` is part of CI).
- `from __future__ import annotations` at the top of new modules.
- Local imports are allowed to break circular deps (the
  `PLC0415` ruff rule is disabled project-wide).
- Public errors derive from `aadr_resolve.AadrResolveError` and pin
  their `exit_code` class attribute; never raise bare `Exception`
  from library code.

## Release process

Maintainer-only. Releases are cut from `main` and published to PyPI
via OIDC trusted publishing (`.github/workflows/publish.yml`).

1. Bump `__version__` in `src/aadr_resolve/__init__.py` and `version`
   in `pyproject.toml` (drop the `.devN` suffix, e.g.
   `0.3.0.dev0` → `0.3.0`).
2. Promote `[Unreleased]` in `CHANGELOG.md` to the new release header
   with today's date.
3. Commit, then tag: `git tag vX.Y.Z && git push --follow-tags`.
4. Create a GitHub Release from the tag — this triggers `publish.yml`,
   which builds the sdist + wheel and uploads to PyPI.
5. Bump `__version__` + `pyproject.toml` to the next `.devN` and add a
   fresh `[Unreleased]` section to the changelog.

After publish, `pip install --upgrade aadr-resolve` may take ~30s for
the PyPI simple-index cache to propagate; pinning the version
(`aadr-resolve==X.Y.Z`) is the reliable check.

## License

By contributing, you agree that your contributions will be licensed
under the MIT License (see [LICENSE](LICENSE)).

# Release process

`aadr-resolve` is published to [PyPI](https://pypi.org/p/aadr-resolve) via GitHub
Actions using **trusted publishing** (OIDC — no API tokens stored in the repo).

## Cutting a release

1. **Bump the version.** Edit `version` in `pyproject.toml` (SemVer: patch for
   fixes, minor for backward-compatible features, major for breaking changes).
2. **Update `CHANGELOG.md`.** Move the `[Unreleased]` entries under a new
   `[X.Y.Z] — YYYY-MM-DD` heading and leave a fresh empty `[Unreleased]`.
3. **Open a `chore: release X.Y.Z` PR** and let CI go green, then merge to `main`.
4. **Tag the merge commit and publish a GitHub Release:**
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z — <summary>" <merge-commit-sha>
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title vX.Y.Z --latest --notes "<release notes>"
   ```
   The tag must point at a commit on `main` whose `ci.yml` run concluded
   `success` — see the gate below.

Publishing the GitHub Release triggers
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml), which:

- **`verify-ci`** — hard gate: queries the GitHub API for the `ci.yml` run on the
  released commit and refuses to publish unless it concluded `success` (waits up
  to 30 min for an in-flight run).
- **`build`** — builds the sdist + wheel with `python -m build`.
- **`publish`** — uploads to PyPI via `pypa/gh-action-pypi-publish` (OIDC).

If `verify-ci` fails, no artifacts are published; fix CI on `main`, then re-run
the failed publish workflow (or delete and recreate the release).

## Trusted-publishing setup (one-time)

PyPI is configured with a **pending/trusted publisher** so the workflow can
publish without a stored token. On PyPI → the `aadr-resolve` project →
*Settings → Publishing*, the trusted publisher points at:

| Field             | Value                          |
| ----------------- | ------------------------------ |
| Owner             | `carstenerickson`              |
| Repository        | `aadr-resolve`                 |
| Workflow filename | `publish.yml`                  |
| Environment       | `pypi`                         |

The `publish` job declares `environment: pypi` and `permissions: id-token: write`
to match. If the repository, workflow filename, or environment name changes,
update the trusted publisher on PyPI to match or publishing will fail.

## Verifying a release

```bash
# GitHub release exists and is marked latest
gh release view vX.Y.Z

# PyPI served the new version (allow a minute for index propagation)
curl -s https://pypi.org/pypi/aadr-resolve/X.Y.Z/json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"
```

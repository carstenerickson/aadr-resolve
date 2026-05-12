# Release process

## Cutting a new version

1. Update `__version__` in `src/aadr_resolve/__init__.py`.
2. Mirror the same version in `pyproject.toml` (`[project] version =`).
3. Move `[Unreleased]` items to a new `[X.Y.Z] — YYYY-MM-DD` heading in
   `CHANGELOG.md`; restart `[Unreleased]` empty.
4. Run the full test matrix locally:
   ```bash
   pytest -ra
   pytest -m "slow and not external" -ra
   ruff check src/ tests/
   ruff format --check src/ tests/
   mypy src/
   ```
5. Commit the version bump + changelog: `release: vX.Y.Z`.
6. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`. Push tag: `git push origin vX.Y.Z`.
7. CI runs against the tag; if green, proceed to PyPI publish.

## PyPI publish

This project is set up for OIDC trusted publishing on PyPI (no API
tokens stored in repo secrets). To enable:

1. On PyPI, create a pending publisher for `aadr-resolve` pointing at
   `carstenerickson/aadr-resolve` and the workflow file
   `.github/workflows/publish.yml` (added when the first release
   ships).
2. The publish workflow uses `pypa/gh-action-pypi-publish@release/v1`
   with `permissions: id-token: write` and runs on `release: published`
   events.
3. To cut a release: create a GitHub Release pointing at the `vX.Y.Z`
   tag. The workflow triggers automatically.

Until the publish workflow + PyPI publisher are set up, manual publish
via `twine` works:

```bash
python -m build
python -m twine upload dist/*
```

## Post-release

- Open a milestone for the next version.
- Bump `__version__` to `X.Y.(Z+1).dev0` (or `X.(Y+1).0.dev0` for a
  minor bump) so subsequent commits don't appear as the released
  version.

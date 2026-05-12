# aadr-resolve

AADR cross-version GeneticID / MasterID join utility for ancient-DNA / population-genetics workflows.

## Status

Day 1 in development. v0.1.0 not yet released.

The HLD pins behavior; the LLD pins implementation. Both live in the cs-wiki:

- HLD: `cs-wiki/projects/aadr-resolve.md`
- LLD: `cs-wiki/projects/aadr-resolve-lld.md`
- Bench-verify report: `cs-wiki/projects/aadr-resolve-bench-verify.md`

## Install (once released)

```bash
pip install aadr-resolve
```

## Canonical use cases

- **Recreate a published cohort against a newer AADR release.** Patterson 2022 used v44.3 sample IDs; today's analysis wants v66.0.
- **Maintain a stable cohort across AADR bumps.** Calibration anchors pinned to specific Genetic IDs need cross-version resolution.
- **Detect AADR sample reclassifications.** When v67 lands, what changed?

## Subcommands

```
aadr-resolve lookup ID --anno-files V1.anno V2.anno     resolve a single sample
aadr-resolve cohort FILE --anno-files V1.anno V2.anno   emit cohort manifest
aadr-resolve diff V1.anno V2.anno                       structured version diff
aadr-resolve join V1.anno V2.anno                       wide-format cross-version table
aadr-resolve schema PATH                                show parsed column schema (diagnostic)
```

## Development

```bash
git clone <repo>
cd aadr-resolve
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -ra
```

## License

MIT.

# scripts/

Maintenance tooling that is **not** part of the runtime library. Used
to regenerate the in-package schema registry when a new AADR release
lands.

| Script | What it does |
|--------|--------------|
| `gen_schemas.py` | Generates `src/aadr_resolve/schemas/class_*.yaml` from real `.anno` headers. |

If you are using `aadr-resolve` as a library or CLI, you don't need
anything in this directory — the generated YAMLs are shipped in the
wheel. This is here so:

1. The schemas have provenance: they're derived from real `.anno`
   files, not hand-typed magic constants.
2. A new AADR release can be supported by anyone with `.anno` access
   — not just the maintainer.

## Regenerating schemas

Two steps: populate a local `aadr-bench/` directory with the six
real `.anno` files, then run the generator.

### 1. Download the `.anno` files

```bash
mkdir -p aadr-bench && cd aadr-bench

# Dataverse fileids captured from the AADR dataset (doi:10.7910/DVN/FFIDCW).
# Dataverse-version → AADR-release map:
#   v3.0  → v44.3
#   v4.0  → v50.0
#   v6.0  → v52.2
#   v7.0  → v54.1
#   v10.0 → v66.0
# v62.0 isn't on Dataverse as a single fileid in the published version;
# you'll need it from a local copy or from the AADR maintainers.

DV=https://dataverse.harvard.edu
curl -L -sSf "$DV/api/access/datafile/7049242"  -o aadr_v44.3_1240K_public.anno
curl -L -sSf "$DV/api/access/datafile/7049266"  -o aadr_v50.0_1240K_public.anno
curl -L -sSf "$DV/api/access/datafile/7052514"  -o v52.2_1240K_public.anno
curl -L -sSf "$DV/api/access/datafile/7052536"  -o v54.1_1240K_public.anno
curl -L -sSf "$DV/api/access/datafile/13663706" -o v66.1240K.aadr.PUB.anno
# For v62.0, put a copy at: aadr-bench/v62.0_HO_public.anno
```

To discover fileids for a new AADR release, query Dataverse:

```bash
curl -sSf "https://dataverse.harvard.edu/api/datasets/:persistentId/versions/:latest-published?persistentId=doi:10.7910/DVN/FFIDCW" \
    | python3 -c "import json,sys; m=json.load(sys.stdin); \
        [print(f\"{f['dataFile']['id']}\t{f['dataFile']['filename']}\") \
         for f in m['data']['files'] if f['dataFile']['filename'].lower().endswith('.anno')]"
```

### 2. Run the generator

```bash
# Regenerate all classes into ./aadr-bench/schemas/ (review before applying)
python scripts/gen_schemas.py

# Or write directly into the in-package registry (then `git diff` to review)
python scripts/gen_schemas.py --in-place

# Regenerate just one class
python scripts/gen_schemas.py E --in-place
```

Then review the diff:

```bash
git diff src/aadr_resolve/schemas/
```

Spot-check the new class's "Fields not present" comment block and
the `notes:` list for accuracy against the raw header.

## Adding a new AADR release

When a new release lands (call it v67), there are two cases:

### Case A — same column layout as an existing class

If the header signature `(ncols, col_0, col_1)` matches an existing
class (e.g., E), append the new version to that class's entry in
`DEFAULT_FILENAMES` at the top of `gen_schemas.py`:

```python
"E": [
    ("v66.0", "v66.1240K.aadr.PUB.anno"),
    ("v67.0", "v67.1240K.aadr.PUB.anno"),  # new
],
```

Then regenerate. The class's `applies_to:` list grows; everything
else stays the same.

### Case B — new column layout (rare; happens every few releases)

The header signature doesn't match any existing class. You need a
new class:

1. Pick the next class ID (F, G, …).
2. Add it to `DEFAULT_FILENAMES`:

   ```python
   "F": [("v67.0", "v67.1240K.aadr.PUB.anno")],
   ```

3. Inspect the new header manually to identify columns whose names
   don't appear in `CANONICAL_FIELDS`. If a canonical field needs a
   new matcher (e.g., AADR renames a column), add the alternative
   to the existing matcher list rather than creating a new canonical
   field.
4. Run `python scripts/gen_schemas.py F`.
5. Add a `class_notes("F")` entry inside `gen_schemas.py` documenting
   what's new in this class (e.g., "12 columns added compared to E").
6. Regenerate; `git diff` to review.
7. Add a regression integration test under `tests/integration/`
   exercising the new class.

The generator's matchers are deliberately conservative — they err
toward "field not present" rather than mis-mapping. Manual review
catches the latter.

## Why this exists

The schema classes (A–F) were derived empirically from real AADR
`.anno` headers. They are not hand-typed: every column position, every
detection signature, every "field not present" note came from running
this script against the six reference releases. Keeping the script
in-tree means:

- The schemas have an in-repo provenance story.
- New AADR releases can be added by anyone, not just the maintainer.
- The matcher priorities and "why is `coverage_1240k` mapped this way
  in class A?" answers live next to the code that produced them.

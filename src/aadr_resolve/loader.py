"""End-to-end .anno loader. Per LLD §3.3."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .errors import IOFailure
from .schema import detect_class, load_all_schemas, normalize_header
from .types import SchemaClass, SchemaClassDef
from .version_inference import cross_check_against_schema, infer_version_label

if TYPE_CHECKING:
    from .annoframe import AnnoFrame


def read_anno(
    path: Path,
    *,
    version_label: str | None = None,
    schema_override: SchemaClass | None = None,
    schemas: dict[SchemaClass, SchemaClassDef] | None = None,
) -> AnnoFrame:
    """Load an .anno end-to-end. Returns a populated AnnoFrame.

    Per LLD §3.3 pipeline:
      1. Ensure schemas pre-loaded (use passed-in if available).
      2. Read raw header line (cheap; surfaces schema-detection failures before
         the full pandas parse).
      3. Detect schema class from header signature.
      4. Infer version label from filename.
      5. Cross-check inferred label vs schema.applies_to; stderr warn on mismatch.
      6. pandas.read_csv with: sep='\\t', dtype=str, na_filter=False,
         quoting=csv.QUOTE_NONE, encoding='utf-8', errors='replace'.
         header=None + skiprows=1 + explicit names = the FULL header (phantom
         included), so the multi-paragraph headers don't roundtrip through
         pandas's parse AND names width matches the data rows, which still carry
         the trailing tab (detection uses the phantom-dropped header instead).
      7. Drop trailing-tab phantom column if present (v54.1 case).
      8. Construct AnnoFrame.
    """
    # Late import to avoid circular dep (annoframe imports loader for the
    # classmethod facade).
    from .annoframe import AnnoFrame

    if not path.exists():
        raise IOFailure(f"file not found: {path}")
    if not path.is_file():
        raise IOFailure(f"not a regular file: {path}")

    if schemas is None:
        schemas = load_all_schemas()

    full_headers = _read_header_only(path)
    # Drop the trailing-tab phantom column BEFORE schema detection. v54.1's header
    # ends with a tab, producing an empty final entry that would otherwise make
    # detect_class fail (no class declares that ncols). Detection uses the dropped
    # header; the FULL header is kept for `names` below, because the data rows still
    # carry the trailing tab — handing read_csv the dropped (narrower) names would
    # make pandas consume the first data column as an index, shifting every field
    # one column right (genetic_id→Master ID, date→SD).
    detect_headers, phantom_dropped = _drop_trailing_phantom_from_headers(full_headers)
    if phantom_dropped:
        sys.stderr.write("WARNING: trailing-tab phantom column dropped from .anno header.\n")
    schema_def = detect_class(detect_headers, schemas, override=schema_override)

    inferred_label, was_inferred = infer_version_label(path, override=version_label)
    if not was_inferred:
        sys.stderr.write(
            f"WARNING: could not infer version label from filename {path.name!r}; "
            f"using {inferred_label!r} (Path.stem). Use --version-label to override.\n"
        )

    # Select the column layout from the ACTUAL header content (not the filename
    # label): for classes whose releases relocate fields under one detection
    # signature, the right layout is observable in the headers. This makes column
    # resolution robust to a wrong/uninferred version label.
    normalized_headers = [normalize_header(h) for h in detect_headers]
    layout_version = schema_def.select_layout_version(
        normalized_headers, fallback_version=inferred_label
    )
    # If the headers picked a different layout than the version label would have,
    # the file is likely mislabeled — say so, but trust the headers.
    if schema_def.version_overrides and layout_version != schema_def.matched_override_key(
        inferred_label
    ):
        sys.stderr.write(
            f"WARNING: header content indicates the "
            f"{layout_version or 'base'} column layout for class "
            f"{schema_def.class_id.value}, which differs from what the version label "
            f"{inferred_label!r} implies; using the header-detected layout.\n"
        )

    warning = cross_check_against_schema(inferred_label, schema_def)
    if warning is not None:
        sys.stderr.write(f"WARNING: {warning}\n")

    # Dedup raw header names — pandas requires unique names. Real .anno files
    # have duplicate column names because AADR uses inline parens as the
    # discriminator (e.g., v66 has 5 "SNPs hit on autosomal targets" columns
    # that differ only by parenthetical panel name). The schema YAML maps
    # canonical fields by COLUMN POSITION, not name, so dedup-via-suffix is
    # display-only and doesn't affect lookups.
    # Use the FULL header (phantom included) so names width matches the data rows,
    # which still carry the trailing tab; the phantom column is dropped from the
    # DataFrame post-parse by _drop_trailing_phantom below.
    unique_names = _dedup_names(full_headers)

    try:
        df = pd.read_csv(
            path,
            sep="\t",
            dtype=str,
            na_filter=False,
            quoting=csv.QUOTE_NONE,
            encoding="utf-8",
            encoding_errors="replace",
            header=None,
            skiprows=1,
            names=unique_names,
            engine="python",
        )
    except OSError as e:
        raise IOFailure(f"failed to read {path}: {e}") from e

    df = _drop_trailing_phantom(df, schema_def)

    return AnnoFrame(
        version=inferred_label,
        schema_class=schema_def.class_id,
        schema_def=schema_def,
        df=df,
        path=path,
        layout_version=layout_version,
    )


def _drop_trailing_phantom_from_headers(headers: list[str]) -> tuple[list[str], bool]:
    """If the last entry's normalize_header form is empty (consequence of a
    trailing tab in the header line), drop it. Returns (headers, was_dropped)."""
    if not headers:
        return headers, False
    if normalize_header(headers[-1]) == "":
        return headers[:-1], True
    return headers, False


def _dedup_names(names: list[str]) -> list[str]:
    """Suffix duplicates with `__dup<N>` so pandas accepts them as DataFrame
    column names. Lookups by column position (the schema YAML's canonical
    method) are unaffected; only df.columns changes."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        if name in seen:
            seen[name] += 1
            out.append(f"{name}__dup{seen[name]}")
        else:
            seen[name] = 0
            out.append(name)
    return out


def _read_header_only(path: Path) -> list[str]:
    """Read just the first line, split on tab. Cheap pre-flight for dispatch."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            line = f.readline().rstrip("\r\n")
    except OSError as e:
        raise IOFailure(f"failed to read header of {path}: {e}") from e
    return line.split("\t")


def _drop_trailing_phantom(df: pd.DataFrame, schema_def: SchemaClassDef) -> pd.DataFrame:
    """If the last column's normalized name is empty (trailing-tab artifact),
    drop it with a stderr warning. Confirms resulting ncols matches schema."""
    if df.shape[1] == 0:
        return df
    last_col_name = str(df.columns[-1])
    if normalize_header(last_col_name) == "":
        sys.stderr.write(
            f"WARNING: trailing-tab phantom column dropped from .anno header "
            f"(was {df.shape[1]} cols, now {df.shape[1] - 1}).\n"
        )
        df = df.iloc[:, :-1]

    # Sanity check: resulting ncols should be in schema's allowed set.
    if df.shape[1] not in schema_def.n_columns_set:
        sys.stderr.write(
            f"WARNING: after phantom-column drop, ncols={df.shape[1]} not in "
            f"schema class {schema_def.class_id.value}'s expected "
            f"{list(schema_def.n_columns_set)}. Proceeding anyway.\n"
        )

    return df

"""Filename -> version label inference. Per LLD §3.2."""

from __future__ import annotations

import re
from pathlib import Path

from .types import SchemaClassDef

_FILENAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # v44.3 / v50.0 / v52.2 / v54.1 + .p1 variants
    (re.compile(r"aadr_v(\d+)\.(\d+)(?:\.p\d+)?_(?:1240K|HO)_public\.anno$"), r"v\1.\2"),
    # v62.0
    (re.compile(r"v(\d+)\.(\d+)_(?:1240k|HO)_public\.anno$"), r"v\1.\2"),
    # v66.0 and panel variants
    (re.compile(r"v(\d+)\.[A-Za-z0-9_]+\.aadr\.PUB\.anno$"), r"v\1.0"),
]


def infer_version_label(path: Path, *, override: str | None = None) -> tuple[str, bool]:
    """Return (label, was_inferred).

    was_inferred=True if a pattern matched or override was used.
    was_inferred=False if the fallback (Path.stem) was used."""
    if override is not None:
        return override, True
    name = path.name
    for pattern, replacement in _FILENAME_PATTERNS:
        m = pattern.search(name)
        if m:
            return pattern.sub(replacement, m.group(0)), True
    return path.stem, False


def label_to_column_prefix(label: str) -> str:
    """'v44.3' -> 'v44_3' for column-name use."""
    return label.replace(".", "_")


def cross_check_against_schema(
    inferred_label: str,
    schema_def: SchemaClassDef,
) -> str | None:
    """Return a stderr warning string if label not in schema.applies_to, else None.

    NOT an error — handles future releases with new schemas cleanly."""
    if inferred_label in schema_def.applies_to:
        return None
    return (
        f"inferred version label {inferred_label!r} but detected schema "
        f"class {schema_def.class_id.value} which applies to "
        f"{list(schema_def.applies_to)}. Proceeding with class "
        f"{schema_def.class_id.value}; use --version-label to suppress this "
        f"warning if you've supplied a renamed .anno file."
    )

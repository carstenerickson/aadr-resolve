"""Schema-registry loader + class dispatcher. Per LLD §3.1."""

from __future__ import annotations

import re
from importlib import resources

import yaml

from .errors import InvariantViolation, SchemaDetectionError
from .types import SchemaClass, SchemaClassDef


def normalize_header(raw: str) -> str:
    """Match-normalized header per HLD §`.anno` loader.

    Strips at first '(' / '[' / ';' / ':' to discard embedded inline-doc
    paragraphs (v66 col 1's 600-byte case; v44/v50 colon-bearing date headers).
    Then lowercase + drop non-word/space + collapse whitespace to '_'.
    """
    s = re.split(r"[(\[;:]", raw, maxsplit=1)[0].strip()
    s = re.sub(r"[^\w\s]", "", s).strip().lower()
    return re.sub(r"\s+", "_", s)


def display_normalize(raw: str) -> str:
    """Display-normalized form: strip at first bracket-class char, trim.

    Used for diagnostic output (`schema` subcommand)."""
    return re.split(r"[(\[;:]", raw, maxsplit=1)[0].strip()


def load_schema(class_id: SchemaClass) -> SchemaClassDef:
    """Load one class YAML from the in-package schemas/ directory."""
    files = resources.files("aadr_resolve.schemas")
    yaml_path = files / f"class_{class_id.value}.yaml"
    try:
        with yaml_path.open("rb") as f:
            data = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        raise InvariantViolation(
            f"failed to load schema YAML for class {class_id.value}: {e}"
        ) from e
    try:
        return SchemaClassDef.from_dict(data)
    except ValueError as e:
        raise InvariantViolation(f"malformed schema YAML for class {class_id.value}: {e}") from e


def load_all_schemas() -> dict[SchemaClass, SchemaClassDef]:
    """Pre-load all class YAMLs.

    Validated: signature uniqueness across the registry. Two classes with the
    same `(n_columns, col_0_normalized, col_1_normalized)` would silently
    break dispatch — caught at load time."""
    schemas = {c: load_schema(c) for c in SchemaClass}

    # Signature-uniqueness invariant: for each (ncols, sig0, sig1), at most one class.
    seen: dict[tuple[int, str, str], SchemaClass] = {}
    for cls, defn in schemas.items():
        for ncols in defn.n_columns_set:
            key = (ncols, *defn.detection_signature)
            if key in seen:
                raise InvariantViolation(
                    f"duplicate schema signature {key} shared by classes "
                    f"{seen[key].value} and {cls.value}. Schema YAMLs are malformed."
                )
            seen[key] = cls

    # version_overrides are hand-authored (gen_schemas.py does not emit them), so
    # validate them at load time: a typo'd field name or out-of-range column would
    # otherwise be silently ignored — reintroducing the wrong-column class of bug
    # overrides exist to fix.
    for defn in schemas.values():
        validate_version_overrides(defn)
    return schemas


def validate_version_overrides(defn: SchemaClassDef) -> None:
    """Raise InvariantViolation if any `version_overrides` entry names a field
    absent from the class or places it outside the valid column range."""
    max_col = max(defn.n_columns_set)
    for ver, cols in defn.version_overrides.items():
        for fld, col in cols.items():
            if fld not in defn.fields:
                raise InvariantViolation(
                    f"class {defn.class_id.value} version_overrides[{ver!r}] names field "
                    f"{fld!r}, which is not in this class's `fields`. Schema YAML is malformed."
                )
            if not 1 <= col <= max_col:
                raise InvariantViolation(
                    f"class {defn.class_id.value} version_overrides[{ver!r}][{fld!r}] = {col} is "
                    f"outside the valid column range 1..{max_col}. Schema YAML is malformed."
                )


def detect_class(
    raw_headers: list[str],
    schemas: dict[SchemaClass, SchemaClassDef],
    *,
    override: SchemaClass | None = None,
) -> SchemaClassDef:
    """Map an .anno header to a SchemaClassDef.

    Algorithm (per LLD §3.1):
      1. If override provided, return that class without signature validation.
      2. Compute (ncols, normalize(col[0]), normalize(col[1])).
      3. For each class, check signature membership. Exactly one match -> return.
      4. Zero matches -> SchemaDetectionError listing observed + known signatures."""
    if override is not None:
        return schemas[override]

    known_with_class: list[tuple[str, int, str, str]] = [
        (defn.class_id.value, n, *defn.detection_signature)
        for defn in schemas.values()
        for n in defn.n_columns_set
    ]

    if len(raw_headers) < 2:
        raise SchemaDetectionError(
            observed=(len(raw_headers), "", ""),
            known=known_with_class,
        )

    ncols = len(raw_headers)
    norm_0 = normalize_header(raw_headers[0])
    norm_1 = normalize_header(raw_headers[1])
    observed = (ncols, norm_0, norm_1)

    for defn in schemas.values():
        if ncols in defn.n_columns_set and defn.detection_signature == (norm_0, norm_1):
            return defn

    raise SchemaDetectionError(observed=observed, known=known_with_class)

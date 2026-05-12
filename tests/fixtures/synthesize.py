"""Deterministic mini-`.anno` synthesizer per LLD §5.2.

Generates fixtures whose header signatures match each schema class A–E. The
generator reads the in-package schema YAMLs (the source of truth for column
positions + normalized headers) so a future schema-registry change
automatically reflows the fixtures.

Usage:
    python -m tests.fixtures.synthesize           # regen all 5 committed fixtures
    python -m tests.fixtures.synthesize --class A # regen just class_A
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

from aadr_resolve.schema import load_schema
from aadr_resolve.types import SchemaClass, SchemaClassDef


@dataclass
class SynthSpec:
    """Parameters for a deterministic mini-.anno."""

    schema_class: SchemaClass
    n_samples: int = 50
    seed: int = 42


def write_anno(spec: SynthSpec, path: Path, *, schema_def: SchemaClassDef | None = None) -> None:
    """Write a synthetic .anno file matching the requested schema class.

    Deterministic across runs given the same SynthSpec. The output has a header
    line that schema.detect_class() correctly resolves to spec.schema_class,
    plus spec.n_samples synthetic data rows."""
    if schema_def is None:
        schema_def = load_schema(spec.schema_class)

    header = _build_header(schema_def)
    rng = random.Random(spec.seed)

    lines = ["\t".join(header)]
    for i in range(spec.n_samples):
        lines.append("\t".join(_synth_row(i, header, schema_def, rng)))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_header(schema_def: SchemaClassDef) -> list[str]:
    """Construct a raw header list whose entries normalize to the YAML's
    `detection_signature` (for cols 0+1) and `normalized_header` (for each
    canonical field). Unmapped positions get `_unmapped_col_<N>` placeholders.
    """
    ncols = schema_def.n_columns_set[0]
    header: list[str | None] = [None] * ncols

    # Place canonical fields at their declared column positions.
    for mapping in schema_def.fields.values():
        idx = mapping.column - 1  # to 0-indexed
        if not 0 <= idx < ncols:
            continue
        header[idx] = mapping.display_header or _title_from_normalized(mapping.normalized_header)

    # Cols 0 and 1 must match detection_signature (normalized).
    sig_0, sig_1 = schema_def.detection_signature
    if header[0] is None:
        header[0] = _title_from_normalized(sig_0)
    if header[1] is None:
        header[1] = _title_from_normalized(sig_1)

    # Fill remaining positions.
    for i in range(ncols):
        if header[i] is None:
            header[i] = f"_unmapped_col_{i + 1}"

    return [str(h) for h in header]


def _title_from_normalized(normalized: str) -> str:
    """Reverse-ish of normalize_header: 'version_id' -> 'Version Id'.
    The result normalizes back to the same string."""
    return " ".join(word.capitalize() for word in normalized.split("_"))


def _synth_row(
    row_idx: int,
    header: list[str],
    schema_def: SchemaClassDef,
    rng: random.Random,
) -> list[str]:
    """Build one synthetic data row."""
    ncols = len(header)
    row: list[str] = [""] * ncols

    def _set(canonical: str, value: str) -> None:
        if canonical in schema_def.fields:
            row[schema_def.fields[canonical].column - 1] = value

    # Identity
    iid = f"Synth{row_idx + 1:04d}"
    suffix = rng.choice(["AG", "DG", "SG"])
    gid_suffixed = f"{iid}.{suffix}"
    # For class A and B, GID column is "Version ID" / "Genetic ID"; we use the
    # suffixed form to keep parse paths exercised.
    _set("genetic_id", gid_suffixed)
    _set("individual_id", iid)
    _set("group_id", rng.choice(["Synth_Test_Population", "Synth_Other_Pop"]))

    # Date (used by Day-2 tests; harmless in Day 1)
    date_bp = rng.randint(0, 50000)
    _set("date_mean_bp", str(date_bp))
    _set("date_sd_bp", str(rng.randint(0, 200)))
    _set("full_date", f"{date_bp} BP")

    # Locality + position
    _set("locality", "SynthSite")
    _set("country_or_political_entity", "SynthCountry")
    _set("latitude", f"{rng.uniform(-60, 70):.4f}")
    _set("longitude", f"{rng.uniform(-180, 180):.4f}")

    # Pulldown + data type + libraries
    _set("pulldown_strategy", "AGAcap")
    _set("data_source_or_type", "1240k_capture")
    _set("n_libraries", "1")

    # Coverage + SNPs
    cov = rng.uniform(0.0, 5.0)
    _set("coverage_1240k", f"{cov:.4f}")
    _set("snps_hit_1240k", str(rng.randint(50000, 1100000)))
    if "snps_hit_ho" in schema_def.fields:
        _set("snps_hit_ho", str(rng.randint(20000, 600000)))

    # Sex + haplogroups
    _set("molecular_sex", rng.choice(["M", "F", "U"]))
    _set("y_haplogroup_isogg", "R1b1a1")
    _set("mtdna_haplogroup", "H2a")
    _set("mtdna_coverage", f"{rng.uniform(5, 200):.2f}")

    # QC
    _set("damage_rate", f"{rng.uniform(0.0, 0.3):.4f}")
    _set("sex_ratio_yx", f"{rng.uniform(0.0, 1.0):.4f}")
    _set("library_type", "ss")
    _set("assessment", "PASS")
    _set("assessment_warnings", "")

    # Skeletal
    _set("skeletal_code", f"SK{row_idx + 1:04d}")
    _set("skeletal_element", "petrous")
    _set("publication", "Synth2026")
    _set("date_method", "OxCal")

    # Class E only
    _set("persistent_genetic_id", str(row_idx + 1))

    return row


def make_i21276_quote_fixture(out_path: Path, *, schema_def: SchemaClassDef | None = None) -> None:
    """1-row v52 (class B) extract with an unbalanced opening `"` — the
    regression case for HLD test 7 (csv.QUOTE_NONE).

    The real sample I21276 in v52.2 has a value like `"381-201 calBCE (...)`
    in the full_date cell (column 12 in class B). The unbalanced opening
    quote tricks pandas's default QUOTE_MINIMAL into hunting for a matching
    closing `"`, which it finds on a much later row — effectively eating
    multiple rows. To reproduce reliably we generate TWO rows so the
    default-quoting parse eats one and emits one, while QUOTE_NONE
    preserves both."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.B)
    header = _build_header(schema_def)
    rng = random.Random(21276)
    # Two rows so the unbalanced quote in row 0 eats row 1 under default
    # quoting (closing quote is found inside row 1's cells), leaving a
    # single corrupted row. QUOTE_NONE retains both.
    row0 = _synth_row(0, header, schema_def, rng)
    row1 = _synth_row(1, header, schema_def, rng)
    if schema_def.has_field("genetic_id"):
        row0[schema_def.fields["genetic_id"].column - 1] = "I21276"
    if schema_def.has_field("individual_id"):
        row0[schema_def.fields["individual_id"].column - 1] = "I21276"
    # Inject the unbalanced opening quote into full_date (matches the real
    # v52 corruption position).
    if schema_def.has_field("full_date"):
        row0[schema_def.fields["full_date"].column - 1] = '"381-201 calBCE (2224 BP, SUERC-104569)'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in [row0, row1])
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def make_v54_trailing_tab_fixture(
    out_path: Path, *, schema_def: SchemaClassDef | None = None
) -> None:
    """Class C (v54.1) fixture with a trailing tab on the header line — the
    regression case for HLD test 8 (trailing-tab phantom-column drop)."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.C)
    header = _build_header(schema_def)
    rng = random.Random(54100)
    row = _synth_row(0, header, schema_def, rng)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Header line ends with a tab (producing a 37th phantom column in v54.1).
    out_path.write_text(
        "\t".join(header) + "\t\n" + "\t".join(row) + "\n",
        encoding="utf-8",
    )


def make_v52_encoding_artifact_fixture(
    out_path: Path, *, schema_def: SchemaClassDef | None = None
) -> None:
    """Class B (v52) fixture with a stray Unicode-replacement-char prefix on
    one row's coverage cell — the regression case from HLD §Coverage
    normalization (v52 has 24 such rows in the wild; loader coerces to NaN)."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.B)
    header = _build_header(schema_def)
    rng = random.Random(5200)
    # 3 normal rows + 1 with the artifact.
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(4)]
    if schema_def.has_field("coverage_1240k"):
        cov_idx = schema_def.fields["coverage_1240k"].column - 1
        # Stray U+FFFD (Unicode replacement char) before the float.
        rows[2][cov_idx] = "�0.158431"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in rows)
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Regenerate the committed mini-.anno fixtures.")
    parser.add_argument(
        "--class",
        dest="cls",
        default="ALL",
        choices=["ALL", "A", "B", "C", "D", "E"],
    )
    parser.add_argument("--n-samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Output directory (default: tests/fixtures/).",
    )
    args = parser.parse_args()

    target_classes = [SchemaClass(args.cls)] if args.cls != "ALL" else list(SchemaClass)
    for cls in target_classes:
        spec = SynthSpec(schema_class=cls, n_samples=args.n_samples, seed=args.seed)
        out_path = args.out_dir / f"tiny_class_{cls.value}.anno"
        write_anno(spec, out_path)
        print(f"  wrote {out_path} ({out_path.stat().st_size} bytes)")

    # Regression fixtures regenerated only when --class=ALL (the default).
    if args.cls == "ALL":
        regression_paths = [
            (args.out_dir / "i21276_quote_v52.anno", make_i21276_quote_fixture),
            (args.out_dir / "v54_trailing_tab.anno", make_v54_trailing_tab_fixture),
            (args.out_dir / "v52_encoding_artifact.anno", make_v52_encoding_artifact_fixture),
        ]
        for out_path, fn in regression_paths:
            fn(out_path)
            print(f"  wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    _cli()

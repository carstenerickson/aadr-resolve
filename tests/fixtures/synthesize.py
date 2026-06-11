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


def _build_header(schema_def: SchemaClassDef, *, ncols: int | None = None) -> list[str]:
    """Construct a raw header list whose entries normalize to the YAML's
    `detection_signature` (for cols 0+1) and `normalized_header` (for each
    canonical field). Unmapped positions get `_unmapped_col_<N>` placeholders.

    Width defaults to the widest declared shape (`max(n_columns_set)`) — the
    canonical full export — rather than the YAML's list order, so a multi-width
    class reflows deterministically. Pass `ncols` to force a specific width
    (e.g. the trailing-tab fixture uses the narrowest, stripped shape).
    """
    if ncols is None:
        ncols = max(schema_def.n_columns_set)
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
    """Class C (v54.1) fixture matching the published 1240K/HO `.anno`: the
    35 mapped columns followed by a trailing tab. The tab yields a phantom
    empty 36th entry that the loader drops, so detection sees 35 — the
    regression case for HLD test 8 (trailing-tab phantom-column drop)."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.C)
    # Build the stripped (narrowest) width so the only extra entry is the
    # phantom produced by the trailing tab below — mirroring the real files.
    header = _build_header(schema_def, ncols=min(schema_def.n_columns_set))
    rng = random.Random(54100)
    row = _synth_row(0, header, schema_def, rng)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Header line ends with a tab (the phantom empty column in published v54.1).
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


def make_loschbour_v66_fixture(out_path: Path, *, schema_def: SchemaClassDef | None = None) -> None:
    """Class E (v66) fixture with `Loschbour` as a 2-row individual:
    `Loschbour.AG` (PGID 33) and `Loschbour.DG` (PGID 39136).

    HLD test 9's canonical case: v66 exposes genetic_id, individual_id,
    and persistent_genetic_id distinctly; multi-row IID is normal."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.E)
    header = _build_header(schema_def)
    rng = random.Random(66001)
    # Build 2 rows for Loschbour + 3 buffer rows for other individuals.
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(5)]

    # Override rows 0 and 1 to be Loschbour's two libraries.
    for row_idx, (gid, pgid) in enumerate([("Loschbour.AG", 33), ("Loschbour.DG", 39136)]):
        if schema_def.has_field("genetic_id"):
            rows[row_idx][schema_def.fields["genetic_id"].column - 1] = gid
        if schema_def.has_field("individual_id"):
            rows[row_idx][schema_def.fields["individual_id"].column - 1] = "Loschbour"
        if schema_def.has_field("persistent_genetic_id"):
            rows[row_idx][schema_def.fields["persistent_genetic_id"].column - 1] = str(pgid)
        if schema_def.has_field("group_id"):
            rows[row_idx][schema_def.fields["group_id"].column - 1] = (
                "Luxembourg_Loschbour_Mesolithic"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in rows)
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def make_uky001_v62_fixture(out_path: Path, *, schema_def: SchemaClassDef | None = None) -> None:
    """Class D (v62) fixture with `UKY001` as a 7-row individual.

    HLD test 10's case: within-version multi-row per IID is normal; lookup
    returns all 7 rows. Real v62 has UKY001 with 7 rows differing in
    library / data-type."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.D)
    header = _build_header(schema_def)
    rng = random.Random(62001)
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(10)]

    # Override first 7 rows to be UKY001 with distinct genetic_id suffixes.
    suffixes = [
        "UKY001.AG",
        "UKY001.DG",
        "UKY001_a.AG",
        "UKY001_b.AG",
        "UKY001_c.AG",
        "UKY001_d.AG",
        "UKY001_e.AG",
    ]
    for row_idx, gid in enumerate(suffixes):
        if schema_def.has_field("genetic_id"):
            rows[row_idx][schema_def.fields["genetic_id"].column - 1] = gid
        if schema_def.has_field("individual_id"):
            rows[row_idx][schema_def.fields["individual_id"].column - 1] = "UKY001"
        if schema_def.has_field("group_id"):
            rows[row_idx][schema_def.fields["group_id"].column - 1] = "Synth_UKY_Population.AG"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in rows)
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def make_loschbour_v54_fixture(out_path: Path, *, schema_def: SchemaClassDef | None = None) -> None:
    """Class C (v54.1) fixture with `I0001` as the Master ID for Loschbour
    (the pre-rename form). Includes `Loschbour_snpAD.DG` as a GID — this
    shared GID is what links v54's I0001 to v62's Loschbour in the
    bench-verify GID-stable detection.

    HLD test 11's reference fixture for the v54.1 side of the chain."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.C)
    header = _build_header(schema_def)
    rng = random.Random(54200)
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(5)]

    # Override rows 0-2 to be I0001 (3 of Loschbour's libraries pre-rename).
    libs = [
        ("I0001", "Luxembourg_Loschbour"),
        ("Loschbour.DG", "Luxembourg_Loschbour.DG"),
        ("Loschbour_snpAD.DG", "Luxembourg_Loschbour.DG"),
    ]
    for row_idx, (gid, group) in enumerate(libs):
        if schema_def.has_field("genetic_id"):
            rows[row_idx][schema_def.fields["genetic_id"].column - 1] = gid
        if schema_def.has_field("individual_id"):
            rows[row_idx][schema_def.fields["individual_id"].column - 1] = "I0001"
        if schema_def.has_field("group_id"):
            rows[row_idx][schema_def.fields["group_id"].column - 1] = group

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in rows)
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def make_loschbour_v62_fixture(out_path: Path, *, schema_def: SchemaClassDef | None = None) -> None:
    """Class D (v62.0) fixture with `Loschbour` as the Master ID (the
    post-rename form). Includes `Loschbour_snpAD.DG` — the same GID present
    in the v54 fixture, providing the bridge witness.

    Paired with loschbour_v54.anno for HLD test 11."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.D)
    header = _build_header(schema_def)
    rng = random.Random(62200)
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(5)]

    libs = [
        ("I0001.AG", "Luxembourg_Mesolithic.AG"),
        ("Loschbour.DG", "Luxembourg_Mesolithic.DG"),
        ("Loschbour_snpAD.DG", "Luxembourg_Mesolithic.DG"),
    ]
    for row_idx, (gid, group) in enumerate(libs):
        if schema_def.has_field("genetic_id"):
            rows[row_idx][schema_def.fields["genetic_id"].column - 1] = gid
        if schema_def.has_field("individual_id"):
            rows[row_idx][schema_def.fields["individual_id"].column - 1] = "Loschbour"
        if schema_def.has_field("group_id"):
            rows[row_idx][schema_def.fields["group_id"].column - 1] = group

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in rows)
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def make_collision_v_old_fixture(
    out_path: Path, *, schema_def: SchemaClassDef | None = None
) -> None:
    """Class C fixture for the cross-lab MID-collision case (HLD test 12).

    Contains one IID `MID-A` with two shared GIDs (`SAMPLE-X.AG`,
    `SAMPLE-Y.AG`). Paired with collision_v_new.anno which assigns the
    same two GIDs to DIFFERENT IIDs (MID-B and MID-C respectively),
    triggering CollisionDetected during detect_bridge."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.C)
    header = _build_header(schema_def)
    rng = random.Random(99001)
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(3)]

    for row_idx, gid in enumerate(["SAMPLE-X.AG", "SAMPLE-Y.AG"]):
        if schema_def.has_field("genetic_id"):
            rows[row_idx][schema_def.fields["genetic_id"].column - 1] = gid
        if schema_def.has_field("individual_id"):
            rows[row_idx][schema_def.fields["individual_id"].column - 1] = "MID-A"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(r) for r in rows)
    out_path.write_text("\t".join(header) + "\n" + body + "\n", encoding="utf-8")


def make_collision_v_new_fixture(
    out_path: Path, *, schema_def: SchemaClassDef | None = None
) -> None:
    """Class D fixture pairing with collision_v_old.anno. Reassigns the
    two shared GIDs to DIFFERENT IIDs: SAMPLE-X.AG -> MID-B, SAMPLE-Y.AG
    -> MID-C. Triggers CollisionDetected when paired with the v_old
    fixture and on_collision='error'."""
    if schema_def is None:
        schema_def = load_schema(SchemaClass.D)
    header = _build_header(schema_def)
    rng = random.Random(99002)
    rows: list[list[str]] = [_synth_row(i, header, schema_def, rng) for i in range(3)]

    cases = [("SAMPLE-X.AG", "MID-B"), ("SAMPLE-Y.AG", "MID-C")]
    for row_idx, (gid, iid) in enumerate(cases):
        if schema_def.has_field("genetic_id"):
            rows[row_idx][schema_def.fields["genetic_id"].column - 1] = gid
        if schema_def.has_field("individual_id"):
            rows[row_idx][schema_def.fields["individual_id"].column - 1] = iid

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

    # Regression + multi-row fixtures regenerated only when --class=ALL.
    if args.cls == "ALL":
        regression_paths = [
            (args.out_dir / "i21276_quote_v52.anno", make_i21276_quote_fixture),
            (args.out_dir / "v54_trailing_tab.anno", make_v54_trailing_tab_fixture),
            (args.out_dir / "v52_encoding_artifact.anno", make_v52_encoding_artifact_fixture),
            (args.out_dir / "loschbour_v66.anno", make_loschbour_v66_fixture),
            (args.out_dir / "uky001_v62.anno", make_uky001_v62_fixture),
            (args.out_dir / "loschbour_v54.anno", make_loschbour_v54_fixture),
            (args.out_dir / "loschbour_v62.anno", make_loschbour_v62_fixture),
            (args.out_dir / "collision_v_old.anno", make_collision_v_old_fixture),
            (args.out_dir / "collision_v_new.anno", make_collision_v_new_fixture),
        ]
        for out_path, fn in regression_paths:
            fn(out_path)
            print(f"  wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    _cli()

#!/usr/bin/env python3
"""Generate aadr-resolve schema YAMLs from real AADR `.anno` files.

This is a maintenance script — not part of the runtime library. Run it
when a new AADR release lands to add a new schema class (or update an
existing one).

Usage:
    python scripts/gen_schemas.py [CLASS ...] [--anno-dir DIR] [--out-dir DIR]

By default, reads `.anno` files from ./aadr-bench/ and writes
class_*.yaml to ./aadr-bench/schemas/. Pass `--in-place` to write
directly into src/aadr_resolve/schemas/ (review the diff before
committing).

See scripts/README.md for the Dataverse setup that populates the
./aadr-bench/ directory.

Single source of truth for the canonical-field → column mapping
algorithm. Anyone investigating "why does aadr-resolve think v66's
PGID lives in column 2?" can read CANONICAL_FIELDS below and the
emit_yaml() function.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Class → version → filename mapping.
# Add new entries here when a new AADR release lands.
# Filenames match what the standard Dataverse fetch in scripts/README.md
# downloads. Override at runtime via --anno-map if your filenames differ.
# ---------------------------------------------------------------------------
DEFAULT_FILENAMES: dict[str, list[tuple[str, str]]] = {
    "A": [
        ("v44.3", "aadr_v44.3_1240K_public.anno"),
        ("v50.0", "aadr_v50.0_1240K_public.anno"),
    ],
    "B": [("v52.2", "v52.2_1240K_public.anno")],
    "C": [("v54.1", "v54.1_1240K_public.anno")],
    "D": [("v62.0", "v62.0_HO_public.anno")],
    "E": [("v66.0", "v66.1240K.aadr.PUB.anno")],
}

# ---------------------------------------------------------------------------
# Matchers receive (raw_header, match_normalized_header) and return bool.
# First match wins per field.
# ---------------------------------------------------------------------------
Matcher = Callable[[str, str], bool]


def match_eq(s: str) -> Matcher:
    return lambda raw, norm: norm == s


def match_starts(s: str) -> Matcher:
    return lambda raw, norm: norm.startswith(s)


def match_raw_contains(*subs: str) -> Matcher:
    """Match against the FULL raw header (parens/brackets INCLUDED)
    case-insensitively. Use when the discriminator content lives
    inside parens — e.g., SNPs-hit 1240k vs HO."""
    return lambda raw, norm: all(sub.lower() in raw.lower() for sub in subs)


# Canonical fields in priority order. Earlier entries' matchers win
# when a column could match multiple fields.
CANONICAL_FIELDS: list[tuple[str, list[Matcher]]] = [
    ("genetic_id", [match_eq("genetic_id"), match_eq("version_id")]),
    ("persistent_genetic_id", [match_eq("persistent_genetic_id")]),
    ("individual_id", [match_eq("individual_id"), match_eq("master_id")]),
    ("skeletal_code", [match_eq("skeletal_code")]),
    ("skeletal_element", [match_eq("skeletal_element")]),
    ("publication", [match_eq("publication"), match_eq("publication_abbreviation")]),
    ("date_method", [match_starts("method_for_determining_date")]),
    ("date_mean_bp", [match_starts("date_mean_in_bp"), match_starts("date_mean_bp")]),
    ("date_sd_bp", [match_starts("date_standard_deviation_in_bp")]),
    ("full_date", [match_starts("full_date")]),
    ("group_id", [match_eq("group_id"), match_eq("groupid")]),
    ("locality", [match_eq("locality")]),
    ("country_or_political_entity", [match_eq("country"), match_eq("political_entity")]),
    ("latitude", [match_eq("lat"), match_eq("latitude")]),
    ("longitude", [match_eq("long"), match_eq("longitude")]),
    ("pulldown_strategy", [match_eq("pulldown_strategy")]),
    ("data_source_or_type", [match_eq("data_source"), match_eq("data_type")]),
    ("n_libraries", [match_eq("no_libraries")]),
    # SNPs-hit discriminator lives inside parens:
    # "SNPs hit on autosomal targets (... 1240k snpset)"
    (
        "snps_hit_1240k",
        [
            match_raw_contains("SNPs hit", "1240k"),
            # Class A has a single SNPs-hit col without a panel discriminator;
            # v44/v50 were 1240k-only, so treat the generic column as 1240k.
            match_raw_contains("SNPs hit on autosomal targets"),
        ],
    ),
    ("snps_hit_ho", [match_raw_contains("SNPs hit", "HO snpset")]),
    # Coverage normalization (canonical Float64 1240k-target coverage).
    # Priority order — most specific first:
    #   B (v52.2):  "1240k coverage (taken from original pulldown where possible)"
    #   C (v54.1):  "1240k coverage (taken from original pulldown where possible)"
    #   E (v66.0):  "Mean coverage on 1.15M autosomal targets for full bam ..."
    #   A (v44/v50): "Coverage on autosomal targets"   (bam-cov fallback;
    #                 not strictly 1240k-specific but the closest semantic
    #                 in v44/v50 which were 1240k-only releases)
    # Class D (v62.0) has NO coverage column — no matcher fires; field
    # absent in YAML.
    (
        "coverage_1240k",
        [
            match_raw_contains("1240k coverage"),
            match_raw_contains("Mean coverage on 1.15M"),
            match_raw_contains("Coverage on autosomal targets"),
        ],
    ),
    ("molecular_sex", [match_eq("molecular_sex"), match_eq("sex")]),
    # Y haplogroup ISOGG version varies (v15.73 vs unversioned vs Yfull 12.03);
    # check raw header rather than match-normalized.
    ("y_haplogroup_isogg", [match_raw_contains("Y haplogroup", "ISOGG")]),
    ("mtdna_haplogroup", [match_starts("mtdna_haplogroup")]),
    ("mtdna_coverage", [match_starts("mtdna_coverage")]),
    ("damage_rate", [match_starts("damage_rate")]),
    ("sex_ratio_yx", [match_starts("sex_ratio")]),
    ("library_type", [match_starts("library_type")]),
    ("assessment", [match_eq("assessment")]),
    ("assessment_warnings", [match_starts("assessment_warning")]),
]


# ---------------------------------------------------------------------------
# Header normalization — kept in-script for self-containedness.
# Matches aadr_resolve.schema.normalize_header / display_normalize behavior.
# ---------------------------------------------------------------------------


def display_normalize(raw: str) -> str:
    """Display form: strip at first (, [, ;, or :."""
    return re.split(r"[(\[;:]", raw, maxsplit=1)[0].strip()


def normalize_match(raw: str) -> str:
    """Match form: display-normalize, lowercase, drop non-word/space, _-join."""
    s = display_normalize(raw)
    s = re.sub(r"[^\w\s]", "", s).strip().lower()
    return re.sub(r"\s+", "_", s)


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        return next(reader)


def find_field(
    raw_headers: list[str], norm_headers: list[str], matchers: list[Matcher]
) -> int | None:
    """Iterate matchers FIRST, columns second — so priority-ordered
    matchers (more-specific-first) win even when the matching column
    appears later in the header order."""
    for m in matchers:
        for i, (raw, norm) in enumerate(zip(raw_headers, norm_headers, strict=True)):
            if m(raw, norm):
                return i + 1  # 1-indexed
    return None


def yaml_escape(s: str) -> str:
    """Minimal YAML string quoting."""
    if any(c in s for c in (":", "#", "'", '"', "\n", "\t")):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if s.strip() != s or s == "" or s.lower() in ("yes", "no", "true", "false", "null", "~"):
        return f'"{s}"'
    return s


def class_notes(class_id: str) -> list[str]:
    """Class-specific notes documented inline in each YAML so the
    in-package registry carries the empirical observations that
    justified each class's existence."""
    if class_id == "A":
        return [
            "v44.3 has 43 columns; v50.0 has 44 (adds 'Age at Death Estimate' at col 12).",
            "Both versions call the per-row identifier 'Version ID' (col 2), not 'Genetic ID'.",
            "v44.3 spells the group column 'GroupID' (no space); v50.0 spells it 'Group ID'. "
            "Match-normalization (lowercase + collapse non-word chars) maps both to 'group_id'.",
            "Both have an 'Index' column at position 1; dropped by class C.",
            "Class A has ONE 'SNPs hit on autosomal targets' column (col 21) without a 1240k vs "
            "HO discriminator — mapped to 'snps_hit_1240k' (v44/v50 were 1240k-only). "
            "'snps_hit_ho' remains unmapped.",
            "'Molecular Sex' field is just 'Sex' in v44/v50 (col 23).",
            "Pulldown Strategy column not present in v44/v50 — this metadata was tracked at a "
            "coarser granularity (the 'Data source' column).",
            "Coverage column (col 20 'Coverage on autosomal targets') is BAM-coverage "
            "(whole-shotgun), not 1240k-specific. Mapped here to 'coverage_1240k' as the "
            "closest semantic proxy; modern array-genotyped samples appear with empty value.",
            "Date column ('Date mean in BP') is at col 10 in v44.3 (extra 'Representative "
            "contact' col 8 before it) and col 9 in v50.0. Both clean integer calBP, no nulls.",
        ]
    if class_id == "B":
        return [
            "Same 'Index' column at position 1 as class A; dropped by class C.",
            "Per-row identifier renamed 'Version ID' -> 'Genetic ID' here (vs class A).",
            "Sample I21276 has an embedded quote character; loader MUST use csv.QUOTE_NONE.",
            "BOTH bam-cov (col 21 'Coverage on autosomal targets') AND 1240k-cov (col 22 "
            "'1240k coverage') are present. Matcher priority maps col 22 -> 'coverage_1240k' "
            "(the canonical native).",
            "v52.2 coverage column has 24 rows with non-numeric values prefixed by stray "
            "U+FFFD (Unicode replacement chars from earlier encoding bugs). Loader should "
            "strip the prefix or coerce-to-NaN. Affects <0.2% of v52 rows.",
        ]
    if class_id == "C":
        return [
            "Index column DROPPED from B (column positions shift by -1).",
            "12 columns dropped from B: index, country (renamed to political_entity), the "
            "BAM-coverage column ('Coverage on autosomal targets'), 4 X-contam ANGSD cols, "
            "4 contamLD cols.",
            "'1240k coverage' (col 20) survives the drop; mapped to 'coverage_1240k'. The "
            "general bam-cov is gone.",
            "Published v54.1 files (1240K and HO) carry a trailing tab, producing a phantom "
            "empty final column that the loader drops; detection then sees 35 columns. A "
            "36-column variant without the trailing tab also loads. Both widths are accepted.",
            "Sample I21276 has an embedded quote character (same as B); loader MUST use "
            "csv.QUOTE_NONE.",
        ]
    if class_id == "D":
        return [
            "Header for col 1 (Genetic ID) contains a ~250-byte inline documentation "
            "paragraph; header normalization (strip-at-first-bracket) is REQUIRED.",
            "Class is similar to C but adds back some columns (doi, link, age_at_death, "
            "ROH, hapConX, etc.) without changing the col 1-2 positions.",
            "NO COVERAGE COLUMN — v62 dropped both bam-cov and 1240k-cov columns. "
            "'coverage_1240k' is unmapped here; AnnoFrame.coverage returns all-NaN Series. "
            "Use coverage_via('snps_hit_1240k') for the derived proxy.",
        ]
    if class_id == "E":
        return [
            "Header for col 1 (Genetic ID) contains a ~600-byte inline documentation "
            "paragraph; header normalization is REQUIRED.",
            "Master ID RENAMED to Individual ID at col 3; new numeric Persistent Genetic ID "
            "column inserted at col 2.",
            "PGID is per-Genetic-ID-row (not per-Individual); useful only as a future-stable "
            "per-row identifier.",
            "Some cells contain TSV-quoted multi-line text with literal '\"\"' double-quotes; "
            "csv.QUOTE_NONE still parses them safely as bytes.",
            "Column 33 and 34 BOTH normalize to 'sum_total_of_roh_segments_20cm'; v66 has a "
            "duplicate-name bug in the upstream .anno. Loader should detect and warn.",
            "Coverage column at col 24 ('Mean coverage on 1.15M autosomal targets for full "
            "bam') is the canonical 1240k-region coverage; mapped to 'coverage_1240k'. v66 "
            "also has col 25 ('Mean coverage on non-targeted autosomal SNPs') which is a "
            "different semantic — left unmapped at the canonical-field level.",
        ]
    return []


def _strip_trailing_phantom(headers: list[str]) -> list[str]:
    """Drop a trailing-tab phantom (an empty final entry) if present, mirroring
    the loader so field/signature detection operates on real columns only."""
    if headers and normalize_match(headers[-1]) == "":
        return headers[:-1]
    return headers


def _accepted_ncols(headers_by_ver: dict[str, list[str]]) -> tuple[list[int], bool]:
    """Return (column-count set detect_class will observe, any-trailing-phantom).

    The loader drops a trailing-tab phantom before detection, so a header that
    carries one is observed at BOTH its stripped width (N-1) and its raw width
    (N). Mirroring that tolerance is what reproduces the verified [35, 36] for
    class C instead of collapsing it back to a single width."""
    accum: set[int] = set()
    trailing_phantom = False
    for headers in headers_by_ver.values():
        width = len(headers)
        if headers and normalize_match(headers[-1]) == "":
            trailing_phantom = True
            accum.update({width - 1, width})
        else:
            accum.add(width)
    return sorted(accum), trailing_phantom


def _n_columns_lines(ncols_set: list[int], trailing_phantom: bool) -> list[str]:
    """The `n_columns:` YAML line, prefixed with an explanatory comment when a
    trailing-tab phantom widens the accepted set."""
    lines: list[str] = []
    if trailing_phantom:
        lines += [
            "# A header in this class carries a trailing tab; the loader drops the phantom",
            "# empty column, so detection sees the stripped width. Accept both the stripped",
            "# and raw widths; the trailing column is unmapped either way, so every field",
            "# below stays at the same position.",
        ]
    lines.append(f"n_columns: {ncols_set if len(ncols_set) > 1 else ncols_set[0]}")
    return lines


def emit_yaml(class_id: str, versions: list[tuple[str, Path]]) -> str:
    """Emit the full YAML text for one schema class."""
    headers_by_ver = {ver: read_header(path) for ver, path in versions}
    ncols_set, trailing_phantom = _accepted_ncols(headers_by_ver)

    rep_ver, _rep_path = versions[0]
    rep_headers = _strip_trailing_phantom(headers_by_ver[rep_ver])
    rep_norm = [normalize_match(h) for h in rep_headers]

    # Detection signature: (ncols set, normalized col[0], normalized col[1])
    sig_col0 = rep_norm[0] if rep_norm else ""
    sig_col1 = rep_norm[1] if len(rep_norm) > 1 else ""

    lines = [
        f"# AADR `.anno` schema class {class_id}",
        "# Generated from real AADR `.anno` headers by scripts/gen_schemas.py.",
        "# To regenerate (or add a new class): see scripts/README.md.",
        "",
        f"class_id: {class_id}",
        "applies_to:",
    ]
    for ver, _ in versions:
        lines.append(f"  - {yaml_escape(ver)}")
    lines += _n_columns_lines(ncols_set, trailing_phantom)
    lines.append("detection_signature:")
    lines.append(f"  col_0_normalized: {yaml_escape(sig_col0)}")
    lines.append(f"  col_1_normalized: {yaml_escape(sig_col1)}")
    lines.append("fields:")

    not_found: list[str] = []
    for canonical, matchers in CANONICAL_FIELDS:
        col = find_field(rep_headers, rep_norm, matchers)
        if col is None:
            not_found.append(canonical)
            continue
        surface = display_normalize(rep_headers[col - 1])
        normalized = rep_norm[col - 1]
        lines.append(f"  {canonical}:")
        lines.append(f"    column: {col}")
        lines.append(f"    normalized_header: {yaml_escape(normalized)}")
        if surface != normalized:
            lines.append(f"    display_header: {yaml_escape(surface)}")

    if not_found:
        lines.append("")
        lines.append("# Fields not present in this class (canonical names unmapped here):")
        for nf in not_found:
            lines.append(f"#   - {nf}")

    notes = class_notes(class_id)
    if notes:
        lines.append("")
        lines.append("notes:")
        for n in notes:
            lines.append(f"  - {yaml_escape(n)}")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate aadr-resolve schema YAMLs from real .anno headers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "classes",
        nargs="*",
        choices=list(DEFAULT_FILENAMES),
        default=None,
        help="Class IDs to regenerate (default: all five).",
    )
    parser.add_argument(
        "--anno-dir",
        type=Path,
        default=Path("aadr-bench"),
        help="Directory containing the .anno files (default: ./aadr-bench/).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Where to write class_*.yaml (default: <anno-dir>/schemas/). "
            "Use --in-place for the in-package registry."
        ),
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Write directly into src/aadr_resolve/schemas/ (review the diff!).",
    )
    args = parser.parse_args()

    if args.in_place and args.out_dir is not None:
        parser.error("--in-place and --out-dir are mutually exclusive")

    if args.in_place:
        out_dir = Path(__file__).resolve().parent.parent / "src" / "aadr_resolve" / "schemas"
    else:
        out_dir = args.out_dir if args.out_dir is not None else args.anno_dir / "schemas"

    out_dir.mkdir(parents=True, exist_ok=True)

    classes = args.classes or list(DEFAULT_FILENAMES)
    for class_id in classes:
        versions: list[tuple[str, Path]] = []
        for version_label, filename in DEFAULT_FILENAMES[class_id]:
            path = args.anno_dir / filename
            if not path.exists():
                print(f"ERROR: {path} not found", file=sys.stderr)
                print(
                    f"  Class {class_id} expects {filename} in {args.anno_dir}; see "
                    "scripts/README.md for the Dataverse setup.",
                    file=sys.stderr,
                )
                return 2
            versions.append((version_label, path))

        yaml_text = emit_yaml(class_id, versions)
        out_path = out_dir / f"class_{class_id}.yaml"
        out_path.write_text(yaml_text)
        nlines = yaml_text.count("\n")
        print(f"  Wrote {out_path} ({len(yaml_text)} bytes, {nlines} lines)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

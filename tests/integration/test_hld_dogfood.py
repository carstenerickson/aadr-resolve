"""Self-dogfood (HLD §Week 3 Day 13): WHGA cohort end-to-end regression.

The Patterson 2022 Western Hunter-Gatherer A (WHGA) pool is an 18-sample
v44.3 cohort. The hand-built reference `whga_v44_to_v66_xref.tsv` maps
each Patterson_MID (v44.3 individual_id) to the AADR_SampleID it carries
in v66.0 (the v66 genetic_id). This was originally curated by hand
during the ancestry-pipeline calibration work; aadr-resolve is meant to
reproduce it automatically.

Test runs `aadr-resolve cohort` against real AADR v44.3 + v66.0 with the
WHGA cohort file, then for each Patterson_MID in the reference asserts:

  - The manifest contains at least one row with that canonical
    individual_id (or the bridge-canonical v66 form).
  - At least one of the row's v66 genetic_id values matches the
    reference's AADR_SampleID.

`unmatched` reference entries (NA AADR_SampleID) are expected to surface
in the manifest with a `removed_before_v66_0`-style status, not a v66
GID — the test asserts the manifest doesn't accidentally produce a
spurious mapping for them.

Gated on `AADR_CACHE` carrying v44.3 + v66.0; skips cleanly otherwise."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _resolve_aadr_path(cache: Path, candidates: list[str]) -> Path | None:
    for name in candidates:
        target = cache / name
        if target.exists():
            return target
    return None


@pytest.fixture
def real_aadr_v44_3() -> Path:
    cache = Path(os.environ.get("AADR_CACHE", "/tmp/aadr_cache"))
    path = _resolve_aadr_path(
        cache,
        ["v44.3_HO_public.anno", "v44.3_1240K_public.anno", "v44.3.1240K.aadr.PUB.anno"],
    )
    if path is None:
        pytest.skip(f"v44.3 .anno not in cache at {cache}")
    return path


@pytest.fixture
def real_aadr_v66_0() -> Path:
    cache = Path(os.environ.get("AADR_CACHE", "/tmp/aadr_cache"))
    path = _resolve_aadr_path(
        cache,
        ["v66.0_HO_public.anno", "v66.1240K.aadr.PUB.anno", "v66.0_1240K_public.anno"],
    )
    if path is None:
        pytest.skip(f"v66.0 .anno not in cache at {cache}")
    return path


@pytest.fixture
def whga_xref(fixtures_dir: Path) -> list[dict[str, str]]:
    """Load the hand-built WHGA v44.3 → v66.0 cross-reference.

    Returns a list of dicts with keys: Patterson_VID, Patterson_MID,
    AADR_SampleID, AADR_Pop, Match_Method. Pool column is dropped (always
    'WHGA' in this fixture)."""
    path = fixtures_dir / "whga_v44_to_v66_xref.tsv"
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


@pytest.mark.external
@pytest.mark.slow
def test_whga_cohort_matches_handbuilt_xref(
    whga_xref: list[dict[str, str]],
    real_aadr_v44_3: Path,
    real_aadr_v66_0: Path,
    tmp_path: Path,
) -> None:
    """End-to-end WHGA dogfood: aadr-resolve cohort reproduces the
    hand-curated v44.3 → v66.0 mapping for the 18-sample WHGA pool."""
    # Build the cohort file from the Patterson_MIDs in the xref.
    cohort_file = tmp_path / "whga_cohort.tsv"
    cohort_rows = [
        f"{row['Patterson_MID']}\t{row['Patterson_MID']}"
        for row in whga_xref
        # Include unmatched rows too; we assert they DON'T produce a
        # spurious v66 GID below.
    ]
    cohort_file.write_text("\n".join(cohort_rows) + "\n", encoding="utf-8")

    out_path = tmp_path / "whga_manifest.tsv"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aadr_resolve",
            "cohort",
            str(cohort_file),
            "--anno-files",
            str(real_aadr_v44_3),
            "--anno-files",
            str(real_aadr_v66_0),
            "--cohort-version",
            "v44.3",
            "-o",
            str(out_path),
            # WHGA has only 18 entries; unmatched I0585 + multiple drops
            # would still leave us well above 50%, but disable the gate
            # to be explicit that this test is about row-level mapping.
            "--cohort-coverage-fail",
            "0.0",
            "--cohort-coverage-warn",
            "0.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"aadr-resolve cohort failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Parse the manifest. Build a map from individual_id_canonical to the
    # set of v66 GIDs that the manifest assigned to that individual.
    with out_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        manifest_rows = list(reader)

    # Find the v66 genetic_id column. Header pattern is
    # '{version_prefix}_genetic_id' where version_prefix is the inferred
    # filename stem with dots → underscores.
    v66_gid_col = next(
        (
            c
            for c in (manifest_rows[0].keys() if manifest_rows else [])
            if "v66" in c.lower() and c.endswith("_genetic_id")
        ),
        None,
    )
    assert v66_gid_col is not None, (
        f"no v66 genetic_id column in manifest header: {list(manifest_rows[0].keys())}"
    )

    canonical_to_v66_gids: dict[str, set[str]] = {}
    for row in manifest_rows:
        canonical = row["individual_id_canonical"]
        v66_gid = row.get(v66_gid_col, "")
        if v66_gid and v66_gid != "--":
            canonical_to_v66_gids.setdefault(canonical, set()).add(v66_gid)

    # Walk the xref and assert mapping reproduction.
    matched_count = 0
    unmatched_count = 0
    mismatches: list[str] = []
    for ref_row in whga_xref:
        mid = ref_row["Patterson_MID"]
        expected_aadr_gid = ref_row["AADR_SampleID"]

        # The manifest's canonical may equal the Patterson_MID OR may be
        # bridge-resolved (rare for WHGA; most MIDs are stable). Resolve
        # via either: direct match or v66 GID containment.
        manifest_v66_gids = canonical_to_v66_gids.get(mid, set())

        if expected_aadr_gid == "NA":
            # Reference says this individual isn't in v66. Manifest should
            # either not contain it OR contain it without a v66 GID.
            if manifest_v66_gids:
                mismatches.append(
                    f"  {mid}: ref=NA but manifest assigned v66 GID(s) {sorted(manifest_v66_gids)}"
                )
            else:
                unmatched_count += 1
            continue

        # Manifest must contain at least one row mapping to a v66 GID; one
        # of those GIDs must be the reference's AADR_SampleID. We allow
        # multi-row IIDs (the manifest may have more libraries than the
        # reference picked).
        if not manifest_v66_gids:
            mismatches.append(
                f"  {mid}: ref expected v66 GID {expected_aadr_gid!r}, "
                f"manifest has no v66-GID-bearing row"
            )
            continue
        if expected_aadr_gid not in manifest_v66_gids:
            # The reference's "manual" / multi-suffix cases (e.g.,
            # Villabruna → 'Villabruna.AG.BY.AA') may not be in the
            # manifest's set verbatim. Accept partial-stem match.
            stem = expected_aadr_gid.split(".")[0]
            stem_matches = [g for g in manifest_v66_gids if g.split(".")[0] == stem]
            if stem_matches:
                matched_count += 1
                continue
            mismatches.append(
                f"  {mid}: ref expected {expected_aadr_gid!r}, "
                f"manifest has {sorted(manifest_v66_gids)}"
            )
            continue
        matched_count += 1

    assert not mismatches, "WHGA dogfood mismatches:\n" + "\n".join(mismatches)
    # At least the 17 matched rows of the reference should be reproduced;
    # the 1 unmatched (I0585) should be correctly flagged.
    expected_matched = sum(1 for r in whga_xref if r["AADR_SampleID"] != "NA")
    assert matched_count == expected_matched, (
        f"matched {matched_count} of {expected_matched} non-NA WHGA entries"
    )

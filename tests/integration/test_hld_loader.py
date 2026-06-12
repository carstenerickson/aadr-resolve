"""HLD tests 6-8: loader's header normalization, csv.QUOTE_NONE, trailing-tab."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.types import SchemaClass


def test_loader_header_normalization(fixtures_dir: Path) -> None:
    """HLD test 6: v66 col 1's long inline-doc header parses correctly via
    strip-at-first-bracket; the canonical 'genetic_id' is found at col 1."""
    af = AnnoFrame.from_path(fixtures_dir / "tiny_class_E.anno")
    assert af.schema_class == SchemaClass.E
    # The genetic_id canonical field maps to column 1; AnnoFrame.genetic_id
    # accessor returns a non-empty string series.
    gids = af.genetic_id
    assert len(gids) == af.n_rows
    assert gids.notna().all()
    # The 'Genetic ID' header in real v66 includes a 600-byte doc paragraph;
    # the normalization pin handles it. Synthetic fixture's header is
    # simpler but the normalizer machinery is exercised the same way.


def test_loader_csv_quote_none_recovers_i21276(fixtures_dir: Path) -> None:
    """HLD test 7: pandas default quoting MISBEHAVES on rows with an
    unbalanced `"` (the real v52 I21276 case); loader's csv.QUOTE_NONE
    retains all rows cleanly."""
    fixture = fixtures_dir / "i21276_quote_v52.anno"

    # QUOTE_NONE retains all rows. Reference behavior.
    df_quote_none = pd.read_csv(
        fixture, sep="\t", dtype=str, na_filter=False, quoting=csv.QUOTE_NONE
    )

    # Default quoting either raises ParserError OR silently drops rows.
    # Both outcomes demonstrate why we don't use the default.
    default_broken = False
    try:
        df_default = pd.read_csv(fixture, sep="\t", dtype=str, na_filter=False)
        if len(df_default) < len(df_quote_none):
            default_broken = True
    except pd.errors.ParserError:
        default_broken = True
    assert default_broken, (
        "default-quoting parse should have either raised or dropped rows on the "
        "unbalanced-quote fixture; if you're seeing this, the fixture isn't "
        "actually exercising the quoting hazard."
    )

    # Tool's loader uses QUOTE_NONE and parses cleanly.
    af = AnnoFrame.from_path(fixture)
    assert af.n_rows == len(df_quote_none)
    assert "I21276" in set(af.individual_id.dropna())


def test_loader_v54_trailing_tab(fixtures_dir: Path) -> None:
    """HLD test 8: class C with trailing-tab header → phantom column dropped;
    final ncols matches the published v54.1 shape (35 mapped cols + trailing tab,
    phantom dropped → 35)."""
    af = AnnoFrame.from_path(fixtures_dir / "v54_trailing_tab.anno")
    assert af.schema_class == SchemaClass.C
    assert af.n_columns == 35  # 35 mapped + trailing-tab phantom, phantom dropped
    # Field alignment: the data rows also carry the trailing tab, so the dropped
    # (narrower) header must NOT be used as read_csv `names` — otherwise pandas
    # consumes the first data column as an index and every field shifts one column
    # right (genetic_id→Master ID, date→SD). Assert no shift.
    assert af.genetic_id.iloc[0] == "Synth0001.DG"  # Genetic ID, not Master ID
    assert af.individual_id.iloc[0] == "Synth0001"
    assert int(af.date_calbp.iloc[0]) == 111  # date mean, not the SD
    # Assert a LATER row too: a regression that mishandles only the index row
    # (row 0) would still pass the checks above. Row 2 has a distinct date mean
    # (29741) vs its SD (66), so a +1 shift there is unambiguous.
    assert af.genetic_id.iloc[2] == "Synth0003.DG"
    assert af.individual_id.iloc[2] == "Synth0003"
    assert int(af.date_calbp.iloc[2]) == 29741  # date mean, not the SD (66)

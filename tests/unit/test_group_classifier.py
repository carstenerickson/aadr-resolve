"""HLD tests 14-20 + LLD-level unit tests for the seven-class classifier."""

from __future__ import annotations

import pytest

from aadr_resolve.group_classifier import classify_all, classify_group_change, suggest_group_lift
from aadr_resolve.types import GroupChangeClass

# === HLD tests 14-19 (one per class) ===


def test_group_class_convention_restructure_suffix() -> None:
    """HLD test 14: `(England_Saxon.AG, England_Saxon)` -> suffix-only change."""
    cls = classify_group_change("England_Saxon.AG", "England_Saxon")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX


def test_group_class_convention_restructure_country() -> None:
    """HLD test 15: `(Czech_IA, Czechia_IA)` -> country rename."""
    cls = classify_group_change("Czech_IA", "Czechia_IA")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_COUNTRY


def test_group_class_convention_restructure_country_with_suffix() -> None:
    """Country rename PLUS suffix strip both apply: Czech_IA.AG -> Czechia_IA."""
    cls = classify_group_change("Czech_IA.AG", "Czechia_IA")
    # The suffix-strip rule fires FIRST (Czech_IA.AG → Czech_IA, doesn't equal
    # Czechia_IA), so it falls through to the country-rename rule.
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_COUNTRY


def test_group_class_convention_restructure_order() -> None:
    """HLD test 16: `(England_MIA_LIA, England_LIA_MIA)` -> token reordering."""
    cls = classify_group_change("England_MIA_LIA", "England_LIA_MIA")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_ORDER


def test_group_class_convention_restructure_punct() -> None:
    """HLD test 17: `(England_Saxon_oAfrica, England_Saxon-oAfrica)` -> _/- swap."""
    cls = classify_group_change("England_Saxon_oAfrica", "England_Saxon-oAfrica")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_PUNCT


def test_group_class_partial() -> None:
    """HLD test 18: `(France_Calvados_N_Norman_lc, France_Calvados_N_Norman)`
    -> partial (new is a proper subset of old's tokens)."""
    cls = classify_group_change("France_Calvados_N_Norman_lc", "France_Calvados_N_Norman")
    assert cls is GroupChangeClass.PARTIAL


def test_group_class_substantive_regroup_yorkshire_arras() -> None:
    """HLD test 19: `(England_EastYorkshire_MIA_LIA.AG, England_LIA_MIA_Arras)` —
    the real v62→v66 case for I0527. Suffix-strip removes `.AG`; remaining
    tokens `{England, EastYorkshire, MIA, LIA}` vs `{England, LIA, MIA, Arras}`
    are not equal, not subset, not punctuation, not country — catchall fires."""
    cls = classify_group_change("England_EastYorkshire_MIA_LIA.AG", "England_LIA_MIA_Arras")
    assert cls is GroupChangeClass.SUBSTANTIVE_REGROUP


# === LLD-level unit tests ===


def test_classify_same_input_unhandled() -> None:
    """Identical inputs aren't changes; the per-pair function still returns
    a class (substantive_regroup as the catchall) but classify_all skips them."""
    out = classify_all([("England_MIA", "England_MIA"), ("Czech_IA", "Czechia_IA")])
    # The identical pair is dropped; the country-rename pair is recorded.
    assert sum(len(v) for v in out.values()) == 1
    assert out[GroupChangeClass.CONVENTION_RESTRUCTURE_COUNTRY] == [("Czech_IA", "Czechia_IA")]


def test_classify_all_returns_all_seven_keys_empty_by_default() -> None:
    """Empty input still returns the full seven-key dict."""
    out = classify_all([])
    assert set(out.keys()) == set(GroupChangeClass)
    assert all(v == [] for v in out.values())


@pytest.mark.parametrize(
    "old, new, expected_class",
    [
        # Class A→AG suffix variants
        ("England_MIA.AG", "England_MIA", GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX),
        ("England_MIA.DG", "England_MIA", GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX),
        ("England_MIA.SG", "England_MIA", GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX),
        # Two-token order swap
        ("A_B", "B_A", GroupChangeClass.CONVENTION_RESTRUCTURE_ORDER),
        # Mixed - / _ punctuation
        ("Group_with_dashes", "Group-with-dashes", GroupChangeClass.CONVENTION_RESTRUCTURE_PUNCT),
    ],
)
def test_classify_known_cases(old: str, new: str, expected_class: GroupChangeClass) -> None:
    assert classify_group_change(old, new) is expected_class


def test_classifier_walks_in_fixed_order() -> None:
    """Suffix takes priority over order: 'A_B.AG' vs 'A_B' is suffix-only,
    not 'order' (even though the tokens differ in their suffix-stripped form)."""
    cls = classify_group_change("A_B.AG", "A_B")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX


# === Prefix-drop (issue #1: Patterson_England_IA → England_IA) ===


def test_group_class_convention_restructure_prefix_drop() -> None:
    """HLD test 20: Patterson_England_IA → England_IA is a known prefix drop,
    not PARTIAL. Regression for issue #1."""
    cls = classify_group_change("Patterson_England_IA", "England_IA")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_PREFIX_DROP


def test_prefix_drop_with_suffix() -> None:
    """Patterson_England_IA.AG → England_IA: suffix stripped first, then prefix
    matched. Both transformations should combine into a single class."""
    cls = classify_group_change("Patterson_England_IA.AG", "England_IA")
    assert cls is GroupChangeClass.CONVENTION_RESTRUCTURE_PREFIX_DROP


def test_prefix_drop_does_not_fire_on_bare_name() -> None:
    """England_IA → England_IA_Ext is PARTIAL (superset), not prefix_drop."""
    cls = classify_group_change("England_IA", "England_IA_Ext")
    assert cls is GroupChangeClass.PARTIAL


@pytest.mark.parametrize(
    "group_id, expected",
    [
        ("Patterson_England_IA", "England_IA"),
        ("Patterson_England_IA.AG", "England_IA"),
        ("Patterson_Scotland_LBA", "Scotland_LBA"),
        ("England_IA", None),
        ("Czech_IA", None),
    ],
)
def test_suggest_group_lift(group_id: str, expected: str | None) -> None:
    assert suggest_group_lift(group_id) == expected


def test_classify_all_includes_prefix_drop_key() -> None:
    """classify_all returns all seven GroupChangeClass keys even with empty input."""
    out = classify_all([])
    assert GroupChangeClass.CONVENTION_RESTRUCTURE_PREFIX_DROP in out

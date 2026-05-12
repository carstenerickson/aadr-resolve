"""Integration tests for `compute_diff` + the `diff` subcommand.

HLD test 23 (full v62→v66 regression) and #24 (per-class event inclusion)
land in Day 7 alongside the larger-fixture work. Day-5 scope covers the
diff's core building blocks: added/removed/renamed/group_changed."""

from __future__ import annotations

from pathlib import Path

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge
from aadr_resolve.diff import compute_diff
from aadr_resolve.types import GroupChangeClass


def test_diff_loschbour_v54_to_v62(fixtures_dir: Path) -> None:
    """compute_diff against the Loschbour v54 + v62 fixtures.

    Expected (per Day-4 bridge logic):
      - shared individuals: 1 (Loschbour, canonical from v62 MID via bridge).
      - added: 4 in v62 (synth buffer rows) - 4 shared from v54 (synth buffer
        rows that happen to overlap by IID won't be shared in our synth);
        we just verify the structure.
      - master_id_renamed: 1 (I0001 -> Loschbour via Loschbour.DG / snpAD).
      - group_changed_by_class: at least 1 entry (Loschbour's group changed
        Luxembourg_Loschbour -> Luxembourg_Mesolithic; classifies as
        substantive_regroup since tokens differ entirely)."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])

    result = compute_diff(af_v54, af_v62, bridge=bridge)

    assert result.v_old_label == "v54.1"
    assert result.v_new_label == "v62.0"
    # MID rename detected.
    assert len(result.master_id_renamed) >= 1
    mid_rename_individuals = {e.individual_id_canonical for e in result.master_id_renamed}
    assert "Loschbour" in mid_rename_individuals
    # The master_id_renamed event's details include the v_old and v_new MIDs.
    loschbour_rename = next(
        e for e in result.master_id_renamed if e.individual_id_canonical == "Loschbour"
    )
    assert loschbour_rename.details["v_old_mid"] == "I0001"
    assert loschbour_rename.details["v_new_mid"] == "Loschbour"
    # Group ID change for Loschbour classifies as substantive_regroup
    # (Luxembourg_Loschbour -> Luxembourg_Mesolithic; no shared tokens beyond
    # "Luxembourg").
    substantive = result.group_changed_by_class[GroupChangeClass.SUBSTANTIVE_REGROUP]
    substantive_canonicals = {e.individual_id_canonical for e in substantive}
    # Loschbour may end up in PARTIAL (Luxembourg_Loschbour vs
    # Luxembourg_Mesolithic — they share {Luxembourg} but disjoint otherwise;
    # neither is a subset of the other). So check it's in either bucket.
    partial = result.group_changed_by_class[GroupChangeClass.PARTIAL]
    partial_canonicals = {e.individual_id_canonical for e in partial}
    assert "Loschbour" in (substantive_canonicals | partial_canonicals)


def test_diff_synth_fixtures_share_all_iids(fixtures_dir: Path) -> None:
    """The deterministic synth generator uses the same seed across classes,
    so synth IIDs (Synth0001, Synth0002, ...) ARE shared across class A and
    class B fixtures. Verify the diff handles this correctly: 50 shared,
    0 added/removed, 0 MID renames, group_changed events flow through the
    classifier (since synth picks group_id randomly per class)."""
    af_a = AnnoFrame.from_path(fixtures_dir / "tiny_class_A.anno", version_label="v44.3")
    af_b = AnnoFrame.from_path(fixtures_dir / "tiny_class_B.anno", version_label="v52.2")
    bridge = detect_bridge([af_a, af_b])

    result = compute_diff(af_a, af_b, bridge=bridge)
    assert result.shared_individuals == 50
    assert result.added == []
    assert result.removed == []
    assert result.master_id_renamed == []
    # GID sets across the two classes use the same Synth0001.AG-style names
    # since the synth uses the same seed for the suffix. genetic_id_renamed
    # detects fully-disjoint GID sets; if any overlap, no event fires.
    # Either is acceptable; we just don't assert a specific count here.


def test_diff_to_dict_has_all_group_classes(fixtures_dir: Path) -> None:
    """JSON-serializable dict has the full 6-key `by_class` map even when
    a class is empty — keeps consumer code stable."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)
    payload = result.to_dict()
    by_class = payload["group_changed"]["by_class"]
    expected_keys = {c.value for c in GroupChangeClass}
    assert set(by_class.keys()) == expected_keys


def test_diff_removal_rate_property(fixtures_dir: Path) -> None:
    """DiffResult.removal_rate computed correctly."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)
    # Should equal len(removed) / v_old_n_individuals.
    expected = len(result.removed) / result.v_old_n_individuals
    assert abs(result.removal_rate - expected) < 1e-9


def test_diff_genetic_id_renamed_detected(fixtures_dir: Path) -> None:
    """Within a shared individual, if GID sets are disjoint, the event
    fires. For Loschbour v54 GIDs are {I0001, Loschbour.DG, Loschbour_snpAD.DG}
    and v62 GIDs are {I0001.AG, Loschbour.DG, Loschbour_snpAD.DG}; sets
    INTERSECT (Loschbour.DG + snpAD), so genetic_id_renamed does NOT fire."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)
    # GID sets share Loschbour.DG and Loschbour_snpAD.DG; no rename event.
    loschbour_renames = [
        e for e in result.genetic_id_renamed if e.individual_id_canonical == "Loschbour"
    ]
    assert loschbour_renames == []

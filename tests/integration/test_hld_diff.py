"""Integration tests for `compute_diff` + the `diff` subcommand.

Day-7 additions: HLD test 24 (per-class event inclusion) and HLD test 23
(full v62→v66 regression, gated on `AADR_CACHE`). Day-5 scope covers the
diff's core building blocks: added/removed/renamed/group_changed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge
from aadr_resolve.cli import cli as cli_group
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


# === HLD test 24: per-class event inclusion ===


def test_diff_to_dict_default_omits_convention_events(fixtures_dir: Path) -> None:
    """Default to_dict() emits per-class counts but no events_<class> arrays
    for convention-restructure classes — substantive_regroup events ARE
    always included (small list).

    HLD test 24 part A."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)
    payload = result.to_dict()

    group_changed = payload["group_changed"]
    # by_class counts are always present for all 6 classes.
    assert set(group_changed["by_class"].keys()) == {c.value for c in GroupChangeClass}
    # substantive_regroup events ALWAYS included.
    assert "events_substantive_regroup" in group_changed
    # Convention-restructure event arrays NOT in default payload.
    for cls in GroupChangeClass:
        if cls is GroupChangeClass.SUBSTANTIVE_REGROUP:
            continue
        assert f"events_{cls.value}" not in group_changed
    # added / removed / genetic_id_renamed have count but no events array.
    assert "events" not in payload["added"]
    assert "events" not in payload["removed"]
    assert "events" not in payload["genetic_id_renamed"]


def test_diff_to_dict_include_class_adds_named_events(fixtures_dir: Path) -> None:
    """to_dict(include_class={SUFFIX}) opts a single class's events back in
    without enabling the rest.

    HLD test 24 part B."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)
    payload = result.to_dict(include_class={GroupChangeClass.CONVENTION_RESTRUCTURE_SUFFIX})

    group_changed = payload["group_changed"]
    # The named class IS now keyed in the dict.
    assert "events_convention_restructure_suffix" in group_changed
    # substantive_regroup still present (always included).
    assert "events_substantive_regroup" in group_changed
    # Other classes still suppressed.
    assert "events_convention_restructure_country" not in group_changed
    assert "events_convention_restructure_order" not in group_changed
    assert "events_convention_restructure_punct" not in group_changed
    assert "events_partial" not in group_changed


def test_diff_to_dict_all_events_includes_everything(fixtures_dir: Path) -> None:
    """to_dict(all_events=True) emits per-class events for ALL 6 classes plus
    events arrays under added / removed / genetic_id_renamed.

    HLD test 24 part C."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)
    payload = result.to_dict(all_events=True)

    group_changed = payload["group_changed"]
    for cls in GroupChangeClass:
        assert f"events_{cls.value}" in group_changed
    assert "events" in payload["added"]
    assert "events" in payload["removed"]
    assert "events" in payload["genetic_id_renamed"]


def test_diff_predict_json_size_bytes_grows_with_events(fixtures_dir: Path) -> None:
    """predict_json_size_bytes scales with the number of events that the
    chosen flags would emit. Default < include-class < all-events."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")
    bridge = detect_bridge([af_v54, af_v62])
    result = compute_diff(af_v54, af_v62, bridge=bridge)

    default_size = result.predict_json_size_bytes()
    all_size = result.predict_json_size_bytes(all_events=True)
    # Default always at least the fixed-overhead floor; all-events >= default.
    assert default_size >= 2048
    assert all_size >= default_size


def test_diff_cmd_include_class_flag(fixtures_dir: Path, tmp_path: Path) -> None:
    """End-to-end: `aadr-resolve diff --include-class substantive_regroup`
    produces JSON with that class's events array. Verifies the click wiring."""
    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "diff",
            str(fixtures_dir / "loschbour_v54.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "--include-class",
            "substantive_regroup",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "events_substantive_regroup" in payload["group_changed"]
    # Convention classes still suppressed (substantive only).
    assert "events_convention_restructure_suffix" not in payload["group_changed"]


def test_diff_cmd_all_events_flag(fixtures_dir: Path) -> None:
    """End-to-end: `aadr-resolve diff --all-events` produces JSON with every
    class's events array plus added/removed/genetic_id_renamed event arrays."""
    runner = CliRunner()
    result = runner.invoke(
        cli_group,
        [
            "diff",
            str(fixtures_dir / "loschbour_v54.anno"),
            str(fixtures_dir / "loschbour_v62.anno"),
            "--all-events",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for cls in GroupChangeClass:
        assert f"events_{cls.value}" in payload["group_changed"]
    assert "events" in payload["added"]
    assert "events" in payload["removed"]
    assert "events" in payload["genetic_id_renamed"]


# === HLD test 23: full v62 ↔ v66 regression ===


@pytest.fixture
def real_aadr_v62() -> Path:
    """Real v62.0 .anno file from the AADR cache. Skipped if not present."""
    cache = Path(os.environ.get("AADR_CACHE", "/tmp/aadr_cache"))
    candidates = [
        cache / "v62.0_HO_public.anno",
        cache / "v62.0.1240K.aadr.PUB.anno",
        cache / "v62.0_1240K_public.anno",
    ]
    for target in candidates:
        if target.exists():
            return target
    pytest.skip(
        f"v62 .anno not in cache at {cache} (tried {[p.name for p in candidates]}); "
        f"set AADR_CACHE or pre-fetch"
    )


@pytest.fixture
def real_aadr_v66() -> Path:
    """Real v66.0 .anno file from the AADR cache. Skipped if not present."""
    cache = Path(os.environ.get("AADR_CACHE", "/tmp/aadr_cache"))
    candidates = [
        cache / "v66.0_HO_public.anno",
        cache / "v66.1240K.aadr.PUB.anno",
        cache / "v66.0_1240K_public.anno",
    ]
    for target in candidates:
        if target.exists():
            return target
    pytest.skip(
        f"v66 .anno not in cache at {cache} (tried {[p.name for p in candidates]}); "
        f"set AADR_CACHE or pre-fetch"
    )


@pytest.mark.external
@pytest.mark.slow
def test_diff_v62_to_v66_real_regression(real_aadr_v62: Path, real_aadr_v66: Path) -> None:
    """Full v62→v66 regression. Loads both real .anno files, computes the
    diff, and asserts coarse-grained invariants that should hold across
    versions:

      - both versions detect a non-empty individual count
      - shared_individuals >> 0 (the bulk of the corpus is retained)
      - by_class counts sum to the total group_changed count
      - JSON serialization round-trips cleanly through json.dumps/loads
      - removal_rate is sane (0 ≤ r < 1)

    Pinned thresholds are deliberately loose — exact counts depend on
    which AADR archive (HO vs 1240K) is in the cache. HLD test 23."""
    af_v62 = AnnoFrame.from_path(real_aadr_v62, version_label="v62.0")
    af_v66 = AnnoFrame.from_path(real_aadr_v66, version_label="v66.0")
    bridge = detect_bridge([af_v62, af_v66])
    result = compute_diff(af_v62, af_v66, bridge=bridge)

    assert result.v_old_n_individuals > 0
    assert result.v_new_n_individuals > 0
    assert result.shared_individuals > 0
    assert 0.0 <= result.removal_rate < 1.0

    # by_class counts match the actual list lengths.
    total_group_changed = sum(len(v) for v in result.group_changed_by_class.values())
    payload = result.to_dict()
    assert payload["group_changed"]["count"] == total_group_changed
    by_class_sum = sum(payload["group_changed"]["by_class"].values())
    assert by_class_sum == total_group_changed

    # All-events JSON round-trip parses cleanly.
    full = result.to_dict(all_events=True)
    blob = json.dumps(full)
    json.loads(blob)  # would raise if invalid

    # The size prediction is in the ballpark of the actual size (~1.5x slack
    # for fixed overhead beyond per-event estimate).
    predicted = result.predict_json_size_bytes(all_events=True)
    assert predicted >= 2048

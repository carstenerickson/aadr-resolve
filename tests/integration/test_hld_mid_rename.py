"""HLD tests 11-13: MID-rename detection + manual override + collision case."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge, load_manual_bridge, merge_with_overrides
from aadr_resolve.errors import CollisionDetected, IOFailure
from aadr_resolve.lookup import lookup_single


def test_mid_rename_loschbour_bridge(fixtures_dir: Path) -> None:
    """HLD test 11: Loschbour I0001 -> Loschbour bridge auto-detected between
    v54.1 (loschbour_v54.anno) and v62.0 (loschbour_v62.anno) via shared GID
    Loschbour_snpAD.DG. `lookup Loschbour --anno-files v54 v62` returns rows
    from BOTH versions with the bridge noted in status."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")

    bridge = detect_bridge([af_v54, af_v62])

    # Bridge should contain at least one Loschbour rename event.
    loschbour_events = [
        e for e in bridge.events if e.mid_old == "I0001" and e.mid_new == "Loschbour"
    ]
    assert loschbour_events, (
        "expected at least one I0001 -> Loschbour event via shared GID; "
        f"got events: {bridge.events}"
    )
    # The witnessing GID must be one of the shared GIDs (Loschbour.DG or
    # Loschbour_snpAD.DG appear in both fixtures).
    witnesses = {e.via_genetic_id for e in loschbour_events}
    assert witnesses & {"Loschbour.DG", "Loschbour_snpAD.DG"}

    # canonical_id maps both v54 I0001 and v62 Loschbour to the same canonical.
    assert bridge.canonical_id("v54.1", "I0001") == "Loschbour"
    assert bridge.canonical_id("v62.0", "Loschbour") == "Loschbour"

    # Lookup chains across versions.
    result = lookup_single("Loschbour", [af_v54, af_v62], bridge=bridge)
    assert result.matched_via == "individual_id"
    assert result.individual_id_canonical == "Loschbour"
    assert "v54.1" in result.per_version
    assert "v62.0" in result.per_version
    assert "individual_id_renamed" in result.status_flags

    # Query the OLD MID and still chain.
    result_old = lookup_single("I0001", [af_v54, af_v62], bridge=bridge)
    assert result_old.individual_id_canonical == "Loschbour"
    assert "v54.1" in result_old.per_version
    assert "v62.0" in result_old.per_version


def test_mid_cross_lab_collision_errors(fixtures_dir: Path) -> None:
    """HLD test 12: cross-lab collision detected. MID-A in v_old maps to
    BOTH MID-B and MID-C in v_new via different shared GIDs.
    detect_bridge raises CollisionDetected (exit 3) by default."""
    af_old = AnnoFrame.from_path(fixtures_dir / "collision_v_old.anno", version_label="v54.1")
    af_new = AnnoFrame.from_path(fixtures_dir / "collision_v_new.anno", version_label="v62.0")

    with pytest.raises(CollisionDetected) as exc_info:
        detect_bridge([af_old, af_new])

    err = exc_info.value
    assert err.v_old == "v54.1"
    assert err.mid_old == "MID-A"
    assert set(err.mids_new) == {"MID-B", "MID-C"}


def test_mid_cross_lab_collision_warn_continues(
    fixtures_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When on_collision='warn', detect_bridge emits a stderr warning and
    continues, picking the alphabetically-first mid_new for the canonical."""
    af_old = AnnoFrame.from_path(fixtures_dir / "collision_v_old.anno", version_label="v54.1")
    af_new = AnnoFrame.from_path(fixtures_dir / "collision_v_new.anno", version_label="v62.0")
    capsys.readouterr()  # clear schema-detection warnings

    bridge = detect_bridge([af_old, af_new], on_collision="warn")
    err = capsys.readouterr().err
    assert "cross-lab MID collision" in err
    assert "MID-A" in err
    # Bridge was still built; events present.
    assert any(e.mid_old == "MID-A" for e in bridge.events)


def test_mid_bridge_manual_override(fixtures_dir: Path, tmp_path: Path) -> None:
    """HLD test 13: --mid-bridge FILE entries layer on auto-detection;
    manual entry beats auto-detection on conflict."""
    af_v54 = AnnoFrame.from_path(fixtures_dir / "loschbour_v54.anno", version_label="v54.1")
    af_v62 = AnnoFrame.from_path(fixtures_dir / "loschbour_v62.anno", version_label="v62.0")

    auto_bridge = detect_bridge([af_v54, af_v62])
    assert auto_bridge.canonical_id("v54.1", "I0001") == "Loschbour"

    # Write a manual override TSV that REDIRECTS I0001 -> FakeID (overriding auto).
    override_path = tmp_path / "manual.tsv"
    override_path.write_text(
        "v_old_label\tmid_old\tv_new_label\tmid_new\nv54.1\tI0001\tv62.0\tFakeID\n",
        encoding="utf-8",
    )

    overrides = load_manual_bridge(override_path)
    assert len(overrides) == 1
    assert overrides[0].mid_old == "I0001"
    assert overrides[0].mid_new == "FakeID"
    assert overrides[0].via_genetic_id is None

    merged, warnings = merge_with_overrides(auto_bridge, overrides)
    # The override replaces the auto-detected I0001->Loschbour mapping.
    assert merged.canonical_id("v54.1", "I0001") == "FakeID"
    # A warning was emitted naming the replacement.
    assert any("manual override" in w and "FakeID" in w for w in warnings)


def test_load_manual_bridge_malformed_header(tmp_path: Path) -> None:
    """Malformed --mid-bridge TSV header raises IOFailure cleanly."""
    bad = tmp_path / "bad.tsv"
    bad.write_text("wrong\theader\n", encoding="utf-8")
    with pytest.raises(IOFailure):
        load_manual_bridge(bad)


def test_load_manual_bridge_skips_comments_and_blanks(tmp_path: Path) -> None:
    """`#` comments and blank lines tolerated."""
    p = tmp_path / "with_comments.tsv"
    p.write_text(
        "v_old_label\tmid_old\tv_new_label\tmid_new\n"
        "# This is a comment\n"
        "\n"
        "v44.3\tI0001\tv66.0\tLoschbour\n",
        encoding="utf-8",
    )
    events = load_manual_bridge(p)
    assert len(events) == 1
    assert events[0].mid_old == "I0001"
    assert events[0].mid_new == "Loschbour"


def test_detect_bridge_no_events_for_clean_pair(fixtures_dir: Path) -> None:
    """Synth class-A + class-B fixtures share no IIDs/GIDs; no events."""
    af_a = AnnoFrame.from_path(fixtures_dir / "tiny_class_A.anno", version_label="v44.3")
    af_b = AnnoFrame.from_path(fixtures_dir / "tiny_class_B.anno", version_label="v52.2")
    bridge = detect_bridge([af_a, af_b])
    # IIDs and GIDs are all distinct synth values; no shared rename events.
    assert bridge.events == []


def test_detect_bridge_single_anno_returns_empty() -> None:
    """detect_bridge([single]) has no consecutive pairs; returns empty bridge."""
    bridge = detect_bridge([])
    assert bridge.events == []

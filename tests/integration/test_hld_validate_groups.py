"""Integration tests for `aadr-resolve validate-groups`. Issue #2 acceptance criteria.

All three cases run against the committed tiny_class_E.anno fixture, which
carries group IDs Synth_Test_Population and Synth_Other_Pop.

  AC-1: lifted  – Patterson_Synth_Test_Population → Synth_Test_Population
                   (found in panel; warning emitted; exit 0)
  AC-2: unresolv – Totally_Unknown_Group_XYZ (not in panel, no known lift;
                   warning emitted; exit non-zero)
  AC-3: valid   – Synth_Test_Population (directly in panel; silent; exit 0)

CLI exit-code tests use subprocess.run (not CliRunner) per LLD §5.1 — CliRunner
does not route through cli.main()'s AadrResolveError → exit-code mapping.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.validate_groups import validate_groups


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_resolve", *args],
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_e(fixtures_dir: Path) -> list[AnnoFrame]:
    return [AnnoFrame.from_path(fixtures_dir / "tiny_class_E.anno", version_label="v66.0")]


# ---------------------------------------------------------------------------
# AC-1: group ID lifted via known prefix drop
# ---------------------------------------------------------------------------


def test_validate_groups_lifted_warns_and_passes(
    fixtures_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Patterson_Synth_Test_Population → Synth_Test_Population is found in the
    v66 panel via the Patterson_ prefix-drop lift.  Result: status=lifted,
    lifted_to set, found_in contains the version label."""
    afs = _load_e(fixtures_dir)
    result = validate_groups(["Patterson_Synth_Test_Population"], afs)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.status == "lifted"
    assert item.lifted_to == "Synth_Test_Population"
    assert "v66.0" in item.found_in
    assert not result.has_failures


def test_validate_groups_lifted_json(fixtures_dir: Path) -> None:
    """JSON output for the lifted case contains all expected keys."""
    afs = _load_e(fixtures_dir)
    result = validate_groups(["Patterson_Synth_Test_Population"], afs)

    item = result.items[0]
    d = {
        "group_id": item.group_id,
        "status": item.status,
        "lifted_to": item.lifted_to,
        "found_in": list(item.found_in),
    }
    assert d["status"] == "lifted"
    assert d["lifted_to"] == "Synth_Test_Population"
    assert "v66.0" in d["found_in"]


# ---------------------------------------------------------------------------
# AC-2: group ID not found and no known lift → unresolvable, exit non-zero
# ---------------------------------------------------------------------------


def test_validate_groups_unresolvable_raises(fixtures_dir: Path) -> None:
    """An unknown group ID that has no known lift is flagged as unresolvable,
    and validate_groups() result.has_failures is True."""
    afs = _load_e(fixtures_dir)
    result = validate_groups(["Totally_Unknown_Group_XYZ"], afs)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.status == "unresolvable"
    assert item.lifted_to is None
    assert result.has_failures


def test_validate_groups_unresolvable_exits_nonzero(fixtures_dir: Path) -> None:
    """The CLI exits non-zero and mentions the unresolvable group ID in stderr."""
    anno_path = str(fixtures_dir / "tiny_class_E.anno")
    proc = _run(["validate-groups", "--anno-files", anno_path, "Totally_Unknown_Group_XYZ"])
    assert proc.returncode != 0
    assert "Totally_Unknown_Group_XYZ" in proc.stderr


# ---------------------------------------------------------------------------
# AC-3: valid group ID → silent, exit 0
# ---------------------------------------------------------------------------


def test_validate_groups_valid_passes_silently(
    fixtures_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A group ID present literally in the panel is valid; no warnings."""
    afs = _load_e(fixtures_dir)
    result = validate_groups(["Synth_Test_Population"], afs)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.status == "valid"
    assert "v66.0" in item.found_in
    assert not result.has_failures


def test_validate_groups_valid_exits_zero(fixtures_dir: Path) -> None:
    """CLI exits 0 and emits no group-ID warnings for a valid group ID."""
    anno_path = str(fixtures_dir / "tiny_class_E.anno")
    proc = _run(
        [
            "--version-label",
            "v66.0",
            "validate-groups",
            "--anno-files",
            anno_path,
            "Synth_Test_Population",
        ]
    )
    assert proc.returncode == 0
    assert "WARNING: group ID" not in proc.stderr


# ---------------------------------------------------------------------------
# Mixed batch: one valid, one lifted, one unresolvable
# ---------------------------------------------------------------------------


def test_validate_groups_mixed_batch(fixtures_dir: Path) -> None:
    """Mixed batch: valid + lifted + unresolvable in one call."""
    afs = _load_e(fixtures_dir)
    result = validate_groups(
        [
            "Synth_Test_Population",  # valid
            "Patterson_Synth_Other_Pop",  # lifted → Synth_Other_Pop
            "Totally_Unknown_Group_XYZ",  # unresolvable
        ],
        afs,
    )

    statuses = {i.group_id: i.status for i in result.items}
    assert statuses["Synth_Test_Population"] == "valid"
    assert statuses["Patterson_Synth_Other_Pop"] == "lifted"
    assert statuses["Totally_Unknown_Group_XYZ"] == "unresolvable"
    assert result.has_failures


# ---------------------------------------------------------------------------
# JSON output via CLI
# ---------------------------------------------------------------------------


def test_validate_groups_json_output(fixtures_dir: Path) -> None:
    """--json flag emits well-formed JSON with status fields."""
    anno_path = str(fixtures_dir / "tiny_class_E.anno")
    proc = _run(
        [
            "validate-groups",
            "--anno-files",
            anno_path,
            "--json",
            "Synth_Test_Population",
            "Patterson_Synth_Other_Pop",
        ]
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert len(data) == 2
    by_gid = {row["group_id"]: row for row in data}
    assert by_gid["Synth_Test_Population"]["status"] == "valid"
    assert by_gid["Patterson_Synth_Other_Pop"]["status"] == "lifted"
    assert by_gid["Patterson_Synth_Other_Pop"]["lifted_to"] == "Synth_Other_Pop"

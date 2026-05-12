"""Standalone perf-benchmark runner per LLD §5.1.

Runs the same pipeline as `test_hld_perf.test_perf_v44_v66_under_2s` but
without pytest infrastructure, so devs can iterate on perf without
re-running the test harness. Prints timing per phase (load, bridge,
library_identity, build_manifest) so regressions are localizable.

Usage:
  AADR_CACHE=/path/to/cache python -m benchmarks.perf_bench

Skips with a clear message if the cache isn't populated."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.bridge import detect_bridge
from aadr_resolve.cohort import build_manifest
from aadr_resolve.library_token import build_all_library_identities


def _resolve_path(cache: Path, candidates: list[str]) -> Path | None:
    for name in candidates:
        target = cache / name
        if target.exists():
            return target
    return None


def main() -> int:
    cache = Path(os.environ.get("AADR_CACHE", "/tmp/aadr_cache"))
    v44 = _resolve_path(
        cache,
        ["v44.3_HO_public.anno", "v44.3_1240K_public.anno", "v44.3.1240K.aadr.PUB.anno"],
    )
    v66 = _resolve_path(
        cache,
        ["v66.0_HO_public.anno", "v66.1240K.aadr.PUB.anno", "v66.0_1240K_public.anno"],
    )
    if v44 is None or v66 is None:
        sys.stderr.write(
            f"AADR cache at {cache} missing v44.3 or v66.0 .anno. "
            f"Set AADR_CACHE to a directory containing the bench fixtures.\n"
        )
        return 1

    print(f"Loading {v44.name} + {v66.name}")
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    af_v44 = AnnoFrame.from_path(v44, version_label="v44.3")
    af_v66 = AnnoFrame.from_path(v66, version_label="v66.0")
    timings["load"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    bridge = detect_bridge([af_v44, af_v66])
    timings["bridge"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    library_identities = build_all_library_identities([af_v44, af_v66], bridge)
    timings["library_identity"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    # 40-sample WHGA-shaped cohort: real IIDs unknown without curation, so
    # sample 40 from the v44 individual_id column.
    v44_iids = [i for i in af_v44.individual_id.tolist() if isinstance(i, str) and i][:40]
    cohort_input: dict[str, str | None] = {iid: None for iid in v44_iids}
    manifest = build_manifest(
        cohort_input,
        [af_v44, af_v66],
        bridge,
        library_identities,
        cohort_version="v44.3",
    )
    timings["build_manifest"] = time.perf_counter() - t0

    total = sum(timings.values())
    print(f"\nPhase timings ({total:.2f}s total):")
    for phase, sec in timings.items():
        print(f"  {phase:20s}  {sec:.3f}s  ({100 * sec / total:.1f}%)")
    v44_count = len({i for i in af_v44.individual_id.tolist() if isinstance(i, str) and i})
    v66_count = len({i for i in af_v66.individual_id.tolist() if isinstance(i, str) and i})
    print(
        f"\nv44 individuals: {v44_count}; "
        f"v66 individuals: {v66_count}; "
        f"manifest rows: {manifest.n_libraries}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())

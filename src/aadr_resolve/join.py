"""Wide-format pairwise cross-version join. Per LLD §3.12 + §4.4.

The join subcommand is the cohort manifest's sibling: same row-per-
(individual × library) cardinality and same output schema, but driven by
the full v_old ∩ v_new intersection of canonical individual_ids instead
of a user-supplied cohort file. We reuse `cohort.build_manifest` so any
schema or status-enum change automatically applies."""

from __future__ import annotations

from .annoframe import AnnoFrame
from .bridge import compute_canonical_version
from .cohort import build_manifest
from .library_token import build_all_library_identities
from .types import CohortManifest, MIDBridge


def compute_join(
    af_old: AnnoFrame,
    af_new: AnnoFrame,
    bridge: MIDBridge,
    *,
    collapse: bool = False,
    gid_preference: tuple[str, ...] = (
        "AG",
        "DG",
        "SG",
        "HO",
        "TW",
        "BY",
        "AA",
        "EC",
        "WGC",
        "bare",
    ),
) -> CohortManifest:
    """Wide-form table of the full v_old × v_new intersection.

    Returns a CohortManifest — same data structure as `cohort` for code
    reuse. The cohort_label column equals individual_id_canonical for
    every row (no user-supplied labels; no propagation).

    `collapse=True` reduces row-per-library to row-per-individual via
    `library_token.collapse_to_individual` with the supplied
    gid_preference (default `AG > DG > SG > HO > TW > BY > AA > EC > WGC
    > bare`). Mirrors the `cohort` subcommand's behavior so a join's TSV
    can be post-processed with the same downstream tooling."""
    anno_frames = [af_old, af_new]

    # The cohort_input for join is every shared canonical individual mapped
    # to its canonical_id as the label. Use the union of individuals from
    # both versions so individuals present in only one version still
    # surface in the manifest (consistent with cohort's full-coverage
    # semantics; status flags record version presence).
    all_canonicals: set[str] = set()
    for af in anno_frames:
        for iid in af.individual_id.tolist():
            if isinstance(iid, str) and iid:
                all_canonicals.add(bridge.canonical_id(af.version, iid))

    cohort_input: dict[str, str | None] = {c: c for c in all_canonicals}

    library_identities = build_all_library_identities(anno_frames, bridge)

    # Canonical version = latest of the two. Used by build_manifest's
    # propagate_labels to find the IID→canonical mapping; with no_propagate
    # the propagation loop is a no-op but the canonical-version anchor still
    # determines which af's IIDs are used to seed canonical_to_label.
    cohort_version = compute_canonical_version([af_old.version, af_new.version])

    return build_manifest(
        cohort_input,
        anno_frames,
        bridge,
        library_identities,
        cohort_version=cohort_version,
        no_propagate=True,
        collapse=collapse,
        gid_preference=gid_preference,
    )

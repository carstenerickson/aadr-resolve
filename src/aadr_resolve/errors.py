"""Exception hierarchy. Per LLD §2.2."""

from __future__ import annotations

from .types import ExitCode


class AadrResolveError(Exception):
    """Base for tool-internal errors. cli.main() catches and exits with exit_code."""

    exit_code: ExitCode = ExitCode.INVARIANT_VIOLATION


class ValidationError(AadrResolveError):
    """Exit-1 gate fired."""

    exit_code = ExitCode.VALIDATION_FAILURE


class IOFailure(AadrResolveError):
    """Read/write failure, output lock held, etc."""

    exit_code = ExitCode.IO_FAILURE


class InvariantViolation(AadrResolveError):
    """Schema unknown, cross-lab MID collision, --on-* error policy triggered."""

    exit_code = ExitCode.INVARIANT_VIOLATION


class UsageError(AadrResolveError):
    """Bad CLI arg combination not catchable by click."""

    exit_code = ExitCode.USAGE_ERROR


class SchemaDetectionError(InvariantViolation):
    """Header signature doesn't match any registered class. Use --schema-override."""

    def __init__(
        self,
        observed: tuple[int, str, str],
        known: list[tuple[str, int, str, str]],
    ) -> None:
        self.observed = observed
        self.known = known
        # Render the known signatures one per line so the user sees what
        # shapes are expected without scanning a 200-char comma-list.
        known_lines = "\n".join(
            f"  class {cls}: ncols={n}, col0={c0!r}, col1={c1!r}" for cls, n, c0, c1 in known
        )
        super().__init__(
            f"unknown .anno schema signature.\n"
            f"  Observed: ncols={observed[0]}, col0={observed[1]!r}, "
            f"col1={observed[2]!r}\n"
            f"  Known signatures:\n{known_lines}\n"
            f"Use --schema-override {{A|B|C|D|E}} to force a class, or "
            f"--version-label LABEL if the filename doesn't match a known pattern."
        )


class MissingNativeFieldError(InvariantViolation):
    """A canonical field is requested but absent in the active schema class."""


class CollisionDetected(InvariantViolation):
    """The GID-stability check found a cross-lab MID collision (HLD §MID rename detection)."""

    def __init__(
        self,
        v_old: str,
        mid_old: str,
        v_new: str,
        mids_new: list[str],
    ) -> None:
        self.v_old = v_old
        self.mid_old = mid_old
        self.v_new = v_new
        self.mids_new = mids_new
        super().__init__(
            f"cross-lab MID collision: {v_old} MID {mid_old!r} maps to multiple "
            f"individuals in {v_new}: {mids_new!r}. "
            f"Use --on-mid-collision warn to continue with stderr warning."
        )

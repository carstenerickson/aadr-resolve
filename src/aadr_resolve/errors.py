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
        known: list[tuple[int, str, str]],
    ) -> None:
        self.observed = observed
        self.known = known
        super().__init__(
            f"unknown .anno schema signature: ncols={observed[0]}, "
            f"col0={observed[1]!r}, col1={observed[2]!r}. "
            f"Known signatures: {known}. "
            f"Use --schema-override CLASS to force a class."
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

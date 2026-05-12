"""Top-level CLI entry point. Per LLD §3.16."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import click

from . import __version__
from .commands.cohort_cmd import cohort_cmd
from .commands.diff_cmd import diff_cmd
from .commands.lookup_cmd import lookup_cmd
from .commands.schema_cmd import schema_cmd
from .errors import AadrResolveError
from .schema import load_all_schemas
from .types import ExitCode, SchemaClass


@click.group()
@click.version_option(version=__version__, prog_name="aadr-resolve")
@click.option(
    "--schema-override",
    type=click.Choice([c.value for c in SchemaClass]),
    default=None,
    help="Force schema class (A|B|C|D|E) instead of auto-detecting from header.",
)
@click.option(
    "--version-label",
    type=str,
    default=None,
    help="Override the inferred version label (e.g., 'v67.0' for a new release).",
)
@click.option(
    "--mid-bridge",
    "mid_bridge_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Manual master_id-rename TSV (Day-2 feature; currently unused).",
)
@click.option(
    "--on-mid-collision",
    type=click.Choice(["error", "warn"]),
    default="error",
    help="Cross-lab MID collision behavior (Day-4 feature).",
)
@click.option("--quiet", is_flag=True, help="Suppress stdout progress block.")
@click.pass_context
def cli(ctx: click.Context, /, **shared_opts: object) -> None:
    """AADR cross-version GeneticID / MasterID join utility."""
    ctx.ensure_object(dict)
    ctx.obj["shared_opts"] = shared_opts


cli.add_command(cohort_cmd, name="cohort")
cli.add_command(diff_cmd, name="diff")
cli.add_command(lookup_cmd, name="lookup")
cli.add_command(schema_cmd, name="schema")


def main() -> int:
    """Top-level entry. Catches AadrResolveError + click usage errors."""
    try:
        # Pre-warm schema registry (~5ms; surfaces YAML-parse errors early).
        _ = load_all_schemas()
        cli(standalone_mode=False)
    except AadrResolveError as e:
        click.echo(f"error: {e}", err=True)
        return int(e.exit_code)
    except click.exceptions.UsageError as e:
        click.echo(f"usage error: {e.message}", err=True)
        return int(ExitCode.USAGE_ERROR)
    except click.exceptions.Abort:
        return int(ExitCode.USAGE_ERROR)
    except SystemExit as e:
        # click raises SystemExit(0) on --help / --version. Treat as success.
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception:
        traceback.print_exc()
        return int(ExitCode.INVARIANT_VIOLATION)
    return 0


if __name__ == "__main__":
    sys.exit(main())

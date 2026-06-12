"""Top-level CLI entry point. Per LLD §3.16."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import click

from . import __version__
from .commands.cohort_cmd import cohort_cmd
from .commands.diff_cmd import diff_cmd
from .commands.join_cmd import join_cmd
from .commands.lookup_cmd import lookup_cmd
from .commands.schema_cmd import schema_cmd
from .commands.validate_groups_cmd import validate_groups_cmd
from .errors import AadrResolveError
from .schema import load_all_schemas
from .types import ExitCode, SchemaClass


@click.group()
@click.version_option(version=__version__, prog_name="aadr-resolve")
@click.option(
    "--schema-override",
    type=click.Choice([c.value for c in SchemaClass]),
    default=None,
    help="Force schema class (A|B|C|D|E|F) instead of auto-detecting from header.",
)
@click.option(
    "--version-label",
    type=str,
    default=None,
    help=(
        "Override the inferred version label (e.g. 'v67.0' for a new release). Sets the "
        "version-keying label only; the column layout is detected from header content, so "
        "this does NOT force a layout when the headers indicate a different one."
    ),
)
@click.option(
    "--mid-bridge",
    "mid_bridge_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Manual master_id-rename TSV layered on top of auto-detected bridge. "
        "Columns: v_old_label, mid_old, v_new_label, mid_new. "
        "File-not-found is reported via IOFailure (exit 2) with the path."
    ),
)
@click.option(
    "--on-mid-collision",
    type=click.Choice(["error", "warn"]),
    default="error",
    help=(
        "Cross-lab MID collision behavior. 'error' (default) exits 3; 'warn' "
        "annotates affected rows with status=library_chain_ambiguous."
    ),
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Suppress stdout progress block (cohort + join + diff write phase).",
)
@click.pass_context
def cli(ctx: click.Context, /, **shared_opts: object) -> None:
    """AADR cross-version GeneticID / MasterID join utility."""
    ctx.ensure_object(dict)
    ctx.obj["shared_opts"] = shared_opts


cli.add_command(cohort_cmd, name="cohort")
cli.add_command(diff_cmd, name="diff")
cli.add_command(join_cmd, name="join")
cli.add_command(lookup_cmd, name="lookup")
cli.add_command(schema_cmd, name="schema")
cli.add_command(validate_groups_cmd, name="validate-groups")


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

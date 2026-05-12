"""Shared pytest fixtures per LLD §5.4."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_resolve.schema import load_all_schemas
from aadr_resolve.types import SchemaClass, SchemaClassDef


@pytest.fixture(scope="session")
def schemas() -> dict[SchemaClass, SchemaClassDef]:
    """Pre-loaded schema registry. Session-scoped: ~5ms load amortized."""
    return load_all_schemas()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the committed fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def tiny_anno_paths(fixtures_dir: Path) -> dict[SchemaClass, Path]:
    """Map SchemaClass -> path to the committed mini-.anno for that class."""
    return {cls: fixtures_dir / f"tiny_class_{cls.value}.anno" for cls in SchemaClass}

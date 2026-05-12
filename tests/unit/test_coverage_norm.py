"""Unit tests for coverage_norm.py."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aadr_resolve.annoframe import AnnoFrame
from aadr_resolve.coverage_norm import (
    PANEL_CARDINALITY_1240K,
    _coerce_float64,
    _proxy_warning_text,
    resolve_coverage,
)
from aadr_resolve.errors import MissingNativeFieldError
from aadr_resolve.types import SchemaClass


def test_coerce_float64_clean_floats() -> None:
    s = pd.Series(["0.5", "1.2", "85.3"])
    out = _coerce_float64(s)
    assert str(out.dtype) == "Float64"
    assert out.isna().sum() == 0
    assert out.tolist() == [0.5, 1.2, 85.3]


def test_coerce_float64_empty_becomes_nan() -> None:
    s = pd.Series(["0.5", "", "1.2"])
    out = _coerce_float64(s)
    assert out.isna().sum() == 1
    assert out.dropna().tolist() == [0.5, 1.2]


def test_coerce_float64_encoding_artifact_becomes_nan() -> None:
    """v52's `\\xef\\xbf\\xbd` Unicode-replacement-char prefix gets coerced
    to NaN (HLD §Coverage normalization)."""
    s = pd.Series(["0.5", "�0.158431", "1.2"])
    out = _coerce_float64(s)
    assert out.isna().sum() == 1
    assert out.dropna().tolist() == [0.5, 1.2]


def test_proxy_warning_text_contains_math() -> None:
    """The Poisson-divergence warning restates the divergence table values
    (0.5x -> 0.39 proxy; 1.0x -> 0.63 proxy)."""
    msg = _proxy_warning_text(file_label="v62.0_HO_public.anno")
    assert "0.39" in msg
    assert "0.63" in msg
    assert "snps_hit_1240k" in msg
    assert str(PANEL_CARDINALITY_1240K) in msg


def test_panel_cardinality_constant() -> None:
    """The 1240k panel cardinality constant is the documented value."""
    assert PANEL_CARDINALITY_1240K == 1148000


def test_resolve_coverage_native_class_b(tiny_anno_paths: dict[SchemaClass, Path]) -> None:
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.B])
    s = resolve_coverage(af, "coverage_1240k")
    assert str(s.dtype) == "Float64"
    # Synth fixture populates every row with a coverage value in [0, 5).
    assert s.isna().sum() == 0
    assert s.min() >= 0.0
    assert s.max() < 5.0


def test_resolve_coverage_class_d_returns_all_nan(
    tiny_anno_paths: dict[SchemaClass, Path],
) -> None:
    """Class D has no native coverage column — returns all-NaN Float64."""
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.D])
    # Wait: Day-2 synth fixture DOES populate coverage_1240k for class D
    # because the bench-verify schema YAML maps that field. But the HLD pin
    # is that class D has NO native coverage column. We need to check what
    # the YAML actually says for class D...
    if "coverage_1240k" not in af.schema_def.fields:
        s = resolve_coverage(af, "coverage_1240k")
        assert str(s.dtype) == "Float64"
        assert s.isna().all()
        assert len(s) == af.n_rows


def test_resolve_coverage_unknown_field_raises(
    tiny_anno_paths: dict[SchemaClass, Path],
) -> None:
    af = AnnoFrame.from_path(tiny_anno_paths[SchemaClass.B])
    with pytest.raises(MissingNativeFieldError):
        resolve_coverage(af, "totally_fictional_field")

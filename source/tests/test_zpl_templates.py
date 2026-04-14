"""Tests for source/zpl_templates.py."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from zpl_templates import (
    build_job_separator,
    build_material_separator,
    build_test_separator,
    sanitize_zpl_field,
)


# ---------------------------------------------------------------------------
# build_material_separator
# ---------------------------------------------------------------------------


def test_material_separator_contains_material():
    result = build_material_separator("WHMR")
    assert b"WHMR" in result


def test_material_separator_starts_with_xa_ends_with_xz():
    result = build_material_separator("WHMR")
    assert result.startswith(b"^XA")
    assert result.rstrip().endswith(b"^XZ")


def test_material_separator_returns_bytes():
    result = build_material_separator("WHMR")
    assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# build_job_separator
# ---------------------------------------------------------------------------


def test_job_separator_contains_both_job_and_material():
    result = build_job_separator("JELPREWIR CL", "WHMR")
    assert b"JELPREWIR CL" in result
    assert b"WHMR" in result


def test_job_separator_order_job_before_material():
    result = build_job_separator("JELPREWIR CL", "WHMR")
    job_idx = result.index(b"JELPREWIR CL")
    mat_idx = result.index(b"WHMR")
    assert job_idx < mat_idx


def test_job_separator_returns_bytes():
    result = build_job_separator("JELPREWIR CL", "WHMR")
    assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# build_test_separator
# ---------------------------------------------------------------------------


def test_test_separator_contains_test_marker():
    result = build_test_separator()
    assert b"TEST" in result


def test_test_separator_contains_branding():
    result = build_test_separator()
    assert b"JobManagerCK" in result


def test_test_separator_starts_with_xa_ends_with_xz():
    result = build_test_separator()
    assert result.startswith(b"^XA")
    assert result.rstrip().endswith(b"^XZ")


# ---------------------------------------------------------------------------
# sanitize_zpl_field
# ---------------------------------------------------------------------------


def test_sanitize_strips_caret():
    assert sanitize_zpl_field("A^B") == "A B"


def test_sanitize_strips_tilde():
    assert sanitize_zpl_field("A~B") == "A B"


def test_sanitize_strips_both_caret_and_tilde():
    assert sanitize_zpl_field("A^B~C") == "A B C"


def test_sanitize_truncates_long_text():
    long_text = "X" * 100
    result = sanitize_zpl_field(long_text)
    assert len(result) <= 60
    assert len(result) == 60


def test_sanitize_strips_whitespace():
    assert sanitize_zpl_field("  hello  ") == "hello"


def test_sanitize_handles_empty_string():
    assert sanitize_zpl_field("") == ""


# ---------------------------------------------------------------------------
# Integration: unsafe characters must not break label templates
# ---------------------------------------------------------------------------


def test_build_with_unsafe_chars():
    """The sanitizer must be applied only to field data, not the template.

    ``^XA`` / ``^XZ`` must remain intact, while ``^`` and ``~`` in the
    user-provided job_name and material must be replaced.
    """
    result = build_job_separator("JOB^1", "WHMR~A")

    # Template carets are still present (start/end markers, field origin, etc.)
    assert result.startswith(b"^XA")
    assert result.rstrip().endswith(b"^XZ")

    # But the *sanitized* versions of the user text must appear...
    assert b"JOB 1" in result
    assert b"WHMR A" in result

    # ...and the raw unsafe forms must NOT appear.
    assert b"JOB^1" not in result
    assert b"WHMR~A" not in result


def test_ascii_encoding_replaces_non_ascii():
    """Non-ASCII characters must be replaced, not raise."""
    result = build_material_separator("WHMR Ω")
    assert isinstance(result, bytes)
    assert b"WHMR" in result
    # Omega is not ASCII, so it should be replaced with '?'
    assert b"?" in result


def test_material_separator_handles_caret_in_input():
    result = build_material_separator("WH^MR")
    assert result.startswith(b"^XA")
    assert b"WH MR" in result
    assert b"WH^MR" not in result

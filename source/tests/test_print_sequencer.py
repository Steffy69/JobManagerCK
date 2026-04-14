"""Tests for source/print_sequencer.py."""

from __future__ import annotations

import os
import sys
from dataclasses import FrozenInstanceError

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from print_sequencer import (  # noqa: E402
    LABEL_KIND,
    SEPARATOR_LABEL_KIND,
    UNKNOWN_MATERIAL,
    PrintItem,
    build_print_sequence,
    compute_peel_order,
    detect_materials_in_job,
    extract_material_from_filename,
    group_ljd_files_by_material,
)


# ---------------------------------------------------------------------------
# extract_material_from_filename
# ---------------------------------------------------------------------------


def test_extract_material_basic():
    assert extract_material_from_filename("JELPREWIR_WHMR_0001.ljd") == (
        "JELPREWIR",
        "WHMR",
        1,
    )


def test_extract_material_multi_underscore_job():
    assert extract_material_from_filename("MY_LONG_JOB_WALNUT_0023.ljd") == (
        "MY_LONG_JOB",
        "WALNUT",
        23,
    )


def test_extract_material_strips_leading_zeros():
    result = extract_material_from_filename("X_Y_00005.ljd")
    assert result is not None
    assert result[2] == 5


def test_extract_material_returns_none_on_missing_segments():
    assert extract_material_from_filename("WHMR_0001.ljd") is None


def test_extract_material_returns_none_on_nonnumeric_board():
    assert extract_material_from_filename("X_Y_FOO.ljd") is None


def test_extract_material_handles_uppercase_extension():
    assert extract_material_from_filename("X_Y_0001.LJD") == ("X", "Y", 1)


def test_extract_material_handles_path():
    assert extract_material_from_filename("/tmp/labels/X_Y_0001.ljd") == ("X", "Y", 1)


def test_extract_material_handles_windows_path():
    assert extract_material_from_filename(r"C:\labels\JOB_WHMR_0007.ljd") == (
        "JOB",
        "WHMR",
        7,
    )


def test_extract_material_returns_none_on_empty():
    assert extract_material_from_filename("") is None


def test_extract_material_returns_none_on_empty_material():
    assert extract_material_from_filename("JOB__0001.ljd") is None


# ---------------------------------------------------------------------------
# group_ljd_files_by_material
# ---------------------------------------------------------------------------


def test_group_by_material_single_material():
    files = [
        "JOB_WHMR_0003.ljd",
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WHMR_0005.ljd",
        "JOB_WHMR_0004.ljd",
    ]
    grouped = group_ljd_files_by_material(files)
    assert list(grouped.keys()) == ["WHMR"]
    boards = [entry[0] for entry in grouped["WHMR"]]
    assert boards == [1, 2, 3, 4, 5]


def test_group_by_material_multiple_materials():
    files = [
        "JOB_WHMR_0002.ljd",
        "JOB_WALNUT_0001.ljd",
        "JOB_WHMR_0001.ljd",
        "JOB_BlackHMR_0001.ljd",
        "JOB_WALNUT_0002.ljd",
    ]
    grouped = group_ljd_files_by_material(files)
    assert set(grouped.keys()) == {"WHMR", "WALNUT", "BlackHMR"}
    assert [e[0] for e in grouped["WHMR"]] == [1, 2]
    assert [e[0] for e in grouped["WALNUT"]] == [1, 2]
    assert [e[0] for e in grouped["BlackHMR"]] == [1]


def test_group_by_material_unknown_goes_to_unknown_bucket():
    files = [
        "JOB_WHMR_0001.ljd",
        "bad_name.ljd",
        "JOB_WHMR_0002.ljd",
        "ALSOBAD.ljd",
    ]
    grouped = group_ljd_files_by_material(files)
    assert UNKNOWN_MATERIAL in grouped
    assert len(grouped[UNKNOWN_MATERIAL]) == 2
    unknown_paths = {entry[1] for entry in grouped[UNKNOWN_MATERIAL]}
    assert "bad_name.ljd" in unknown_paths
    assert "ALSOBAD.ljd" in unknown_paths
    assert len(grouped["WHMR"]) == 2


def test_group_by_material_preserves_full_paths():
    files = ["/abs/path/JOB_WHMR_0001.ljd"]
    grouped = group_ljd_files_by_material(files)
    assert grouped["WHMR"][0][1] == "/abs/path/JOB_WHMR_0001.ljd"


# ---------------------------------------------------------------------------
# compute_peel_order
# ---------------------------------------------------------------------------


def test_compute_peel_order_all_listed():
    result = compute_peel_order(
        materials_present=["WHMR", "WALNUT", "BlackHMR"],
        material_priority=("WHMR", "WALNUT", "BlackHMR", "MO", "HG"),
        material_counts={"WHMR": 34, "WALNUT": 16, "BlackHMR": 3},
    )
    assert result == ["WHMR", "WALNUT", "BlackHMR"]


def test_compute_peel_order_some_unlisted():
    result = compute_peel_order(
        materials_present=["WHMR", "MYSTERY", "WALNUT"],
        material_priority=("WHMR", "WALNUT"),
        material_counts={"WHMR": 10, "WALNUT": 5, "MYSTERY": 2},
    )
    assert result == ["WHMR", "WALNUT", "MYSTERY"]


def test_compute_peel_order_empty_priority():
    result = compute_peel_order(
        materials_present=["A", "B", "C"],
        material_priority=(),
        material_counts={"A": 5, "B": 20, "C": 10},
    )
    assert result == ["B", "C", "A"]


def test_compute_peel_order_stable_ties():
    result = compute_peel_order(
        materials_present=["B", "A", "C"],
        material_priority=(),
        material_counts={"A": 5, "B": 5, "C": 5},
    )
    assert result == ["A", "B", "C"]


def test_compute_peel_order_drops_priority_materials_not_present():
    result = compute_peel_order(
        materials_present=["WHMR"],
        material_priority=("WHMR", "WALNUT", "BlackHMR"),
        material_counts={"WHMR": 10},
    )
    assert result == ["WHMR"]


def test_compute_peel_order_mixed_listed_and_unlisted_counts():
    result = compute_peel_order(
        materials_present=["WHMR", "X", "Y", "Z"],
        material_priority=("WHMR",),
        material_counts={"WHMR": 1, "X": 10, "Y": 5, "Z": 20},
    )
    assert result == ["WHMR", "Z", "X", "Y"]


# ---------------------------------------------------------------------------
# build_print_sequence
# ---------------------------------------------------------------------------


def test_build_sequence_single_material_no_separators():
    files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WHMR_0003.ljd",
    ]
    seq = build_print_sequence(
        job_name="JOB",
        ljd_files=files,
        material_priority=("WHMR",),
        reverse_within=True,
        include_separators=False,
    )
    assert len(seq) == 3
    assert seq[0].board_number == 3
    assert seq[1].board_number == 2
    assert seq[2].board_number == 1
    assert all(item.kind == LABEL_KIND for item in seq)
    assert all(item.material == "WHMR" for item in seq)


def test_build_sequence_single_material_with_separators():
    files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WHMR_0003.ljd",
    ]
    seq = build_print_sequence(
        job_name="JOB",
        ljd_files=files,
        material_priority=("WHMR",),
        reverse_within=True,
        include_separators=True,
    )
    assert len(seq) == 4
    assert seq[0].kind == LABEL_KIND and seq[0].board_number == 3
    assert seq[1].kind == LABEL_KIND and seq[1].board_number == 2
    assert seq[2].kind == LABEL_KIND and seq[2].board_number == 1
    topmost = seq[3]
    assert topmost.kind == SEPARATOR_LABEL_KIND
    assert topmost.is_job_separator is True
    assert topmost.material == "WHMR"
    assert topmost.job_name == "JOB"
    assert topmost.file_path == ""
    assert topmost.board_number is None


def test_build_sequence_jelprewir_full_example():
    """Regression test: exact print order from V2.1-PLAN.md."""
    whmr_files = [f"JELPREWIRCL_WHMR_{i:04d}.ljd" for i in range(1, 35)]
    walnut_files = [f"JELPREWIRCL_WALNUT_{i:04d}.ljd" for i in range(1, 17)]
    blackhmr_files = [f"JELPREWIRCL_BlackHMR_{i:04d}.ljd" for i in range(1, 4)]
    all_files = whmr_files + walnut_files + blackhmr_files

    seq = build_print_sequence(
        job_name="JELPREWIR CL",
        ljd_files=all_files,
        material_priority=("WHMR", "WALNUT", "BlackHMR", "MO", "HG"),
        reverse_within=True,
        include_separators=True,
    )

    assert len(seq) == 56

    # Items 0..2 — BlackHMR 3, 2, 1 (deepest in stack, printed first)
    for idx, expected_board in enumerate([3, 2, 1]):
        item = seq[idx]
        assert item.kind == LABEL_KIND
        assert item.material == "BlackHMR"
        assert item.board_number == expected_board

    # Item 3 — material separator for BlackHMR
    sep_black = seq[3]
    assert sep_black.kind == SEPARATOR_LABEL_KIND
    assert sep_black.material == "BlackHMR"
    assert sep_black.is_job_separator is False
    assert sep_black.job_name == ""

    # Items 4..19 — WALNUT 16, 15, ..., 1
    for idx in range(16):
        item = seq[4 + idx]
        assert item.kind == LABEL_KIND
        assert item.material == "WALNUT"
        assert item.board_number == 16 - idx

    # Item 20 — material separator for WALNUT
    sep_walnut = seq[20]
    assert sep_walnut.kind == SEPARATOR_LABEL_KIND
    assert sep_walnut.material == "WALNUT"
    assert sep_walnut.is_job_separator is False
    assert sep_walnut.job_name == ""

    # Items 21..54 — WHMR 34, 33, ..., 1
    for idx in range(34):
        item = seq[21 + idx]
        assert item.kind == LABEL_KIND
        assert item.material == "WHMR"
        assert item.board_number == 34 - idx

    # Item 55 — JOB separator, topmost in stack
    job_sep = seq[55]
    assert job_sep.kind == SEPARATOR_LABEL_KIND
    assert job_sep.is_job_separator is True
    assert job_sep.job_name == "JELPREWIR CL"
    assert job_sep.material == "WHMR"
    assert job_sep.file_path == ""
    assert job_sep.board_number is None


def test_build_sequence_unlisted_material_uses_count_fallback():
    files = (
        [f"JOB_X_{i:04d}.ljd" for i in range(1, 11)]
        + [f"JOB_Y_{i:04d}.ljd" for i in range(1, 6)]
        + [f"JOB_Z_{i:04d}.ljd" for i in range(1, 21)]
    )
    seq = build_print_sequence(
        job_name="JOB",
        ljd_files=files,
        material_priority=(),
        reverse_within=True,
        include_separators=False,
    )
    # Peel order (top = first peeled) is Z (20), X (10), Y (5).
    # Print order reverses that: Y first, then X, then Z.
    materials_in_print_order = [item.material for item in seq]
    first_y = materials_in_print_order.index("Y")
    first_x = materials_in_print_order.index("X")
    first_z = materials_in_print_order.index("Z")
    assert first_y < first_x < first_z

    # Y should appear in descending board order first (5, 4, 3, 2, 1).
    y_items = [item for item in seq if item.material == "Y"]
    assert [it.board_number for it in y_items] == [5, 4, 3, 2, 1]

    # Z last, also descending inside its block.
    z_items = [item for item in seq if item.material == "Z"]
    assert [it.board_number for it in z_items] == list(range(20, 0, -1))


def test_build_sequence_reverse_within_false():
    files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WHMR_0003.ljd",
    ]
    seq = build_print_sequence(
        job_name="JOB",
        ljd_files=files,
        material_priority=("WHMR",),
        reverse_within=False,
        include_separators=False,
    )
    assert [item.board_number for item in seq] == [1, 2, 3]


def test_build_sequence_empty_files():
    assert (
        build_print_sequence(
            job_name="JOB",
            ljd_files=[],
            material_priority=("WHMR",),
        )
        == []
    )


def test_build_sequence_only_unknown_files():
    files = ["bad_a.ljd", "bad_b.ljd"]
    seq = build_print_sequence(
        job_name="JOB",
        ljd_files=files,
        material_priority=(),
        reverse_within=True,
        include_separators=True,
    )
    # 2 labels + 1 job separator on top.
    assert len(seq) == 3
    labels = [it for it in seq if it.kind == LABEL_KIND]
    separators = [it for it in seq if it.kind == SEPARATOR_LABEL_KIND]
    assert len(labels) == 2
    assert all(it.material == UNKNOWN_MATERIAL for it in labels)
    assert len(separators) == 1
    assert separators[0].is_job_separator is True
    assert separators[0].material == UNKNOWN_MATERIAL


def test_build_sequence_two_materials_both_have_material_separators_except_top():
    files = (
        ["JOB_WHMR_0001.ljd", "JOB_WHMR_0002.ljd"]
        + ["JOB_WALNUT_0001.ljd"]
    )
    seq = build_print_sequence(
        job_name="JOB",
        ljd_files=files,
        material_priority=("WHMR", "WALNUT"),
        reverse_within=True,
        include_separators=True,
    )
    # Peel order: WHMR first, then WALNUT.
    # Peel items: [SEP_JOB(WHMR), WHMR1, WHMR2, SEP(WALNUT), WALNUT1]
    # Print order reversed: [WALNUT1, SEP(WALNUT), WHMR2, WHMR1, SEP_JOB(WHMR)]
    assert len(seq) == 5
    assert seq[0].material == "WALNUT" and seq[0].board_number == 1
    assert seq[1].kind == SEPARATOR_LABEL_KIND
    assert seq[1].material == "WALNUT"
    assert seq[1].is_job_separator is False
    assert seq[2].material == "WHMR" and seq[2].board_number == 2
    assert seq[3].material == "WHMR" and seq[3].board_number == 1
    assert seq[4].kind == SEPARATOR_LABEL_KIND
    assert seq[4].material == "WHMR"
    assert seq[4].is_job_separator is True
    assert seq[4].job_name == "JOB"


# ---------------------------------------------------------------------------
# detect_materials_in_job
# ---------------------------------------------------------------------------


def test_detect_materials_respects_default_priority():
    files = (
        [f"JOB_WHMR_{i:04d}.ljd" for i in range(1, 4)]
        + [f"JOB_WALNUT_{i:04d}.ljd" for i in range(1, 3)]
        + ["JOB_BlackHMR_0001.ljd"]
    )
    result = detect_materials_in_job(files, default_priority=("WHMR", "WALNUT"))
    assert [material for material, _ in result] == ["WHMR", "WALNUT", "BlackHMR"]
    assert dict(result) == {"WHMR": 3, "WALNUT": 2, "BlackHMR": 1}


def test_detect_materials_whmr_always_first():
    files = [
        "JOB_WALNUT_0001.ljd",
        "JOB_WALNUT_0002.ljd",
        "JOB_WHMR_0001.ljd",
    ]
    result = detect_materials_in_job(files, default_priority=())
    assert [material for material, _ in result] == ["WHMR", "WALNUT"]


def test_detect_materials_unlisted_by_count_desc():
    files = (
        [f"JOB_X_{i:04d}.ljd" for i in range(1, 11)]  # 10
        + [f"JOB_Y_{i:04d}.ljd" for i in range(1, 21)]  # 20
        + [f"JOB_Z_{i:04d}.ljd" for i in range(1, 6)]  # 5
    )
    result = detect_materials_in_job(files, default_priority=())
    # No WHMR in the files, so WHMR fallback is a no-op. Count-desc + alpha
    # tiebreak gives [Y, X, Z].
    assert [material for material, _ in result] == ["Y", "X", "Z"]
    assert dict(result) == {"Y": 20, "X": 10, "Z": 5}


def test_detect_materials_counts_correct():
    files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WHMR_0003.ljd",
        "JOB_WALNUT_0001.ljd",
        "JOB_WALNUT_0002.ljd",
        "JOB_BlackHMR_0001.ljd",
    ]
    result = detect_materials_in_job(files, default_priority=("WHMR",))
    counts = dict(result)
    assert counts["WHMR"] == 3
    assert counts["WALNUT"] == 2
    assert counts["BlackHMR"] == 1


def test_detect_materials_empty_files_returns_empty():
    assert detect_materials_in_job([], default_priority=("WHMR",)) == []


def test_detect_materials_includes_unknown_bucket():
    files = [
        "JOB_WHMR_0001.ljd",
        "bad_filename.ljd",
        "another_bad.ljd",
    ]
    result = detect_materials_in_job(files, default_priority=("WHMR",))
    materials = [material for material, _ in result]
    assert "WHMR" in materials
    assert UNKNOWN_MATERIAL in materials
    # WHMR is priority-first, UNKNOWN bucket gets count-based slot after.
    assert materials.index("WHMR") < materials.index(UNKNOWN_MATERIAL)


# ---------------------------------------------------------------------------
# PrintItem frozen dataclass
# ---------------------------------------------------------------------------


def test_print_item_frozen():
    item = PrintItem(
        kind=LABEL_KIND,
        file_path="x.ljd",
        material="WHMR",
        board_number=1,
    )
    with pytest.raises(FrozenInstanceError):
        item.board_number = 2  # type: ignore[misc]


def test_print_item_defaults():
    item = PrintItem(
        kind=LABEL_KIND,
        file_path="x.ljd",
        material="WHMR",
        board_number=1,
    )
    assert item.is_job_separator is False
    assert item.job_name == ""

"""Pure logic for building Zebra label print sequences.

The Zebra GC420D emits a stack of labels. First printed = bottom of stack =
last peeled. Last printed = top of stack = first peeled. All public functions
in this module are pure: no I/O, no globals, no side effects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

LABEL_KIND = "label"
SEPARATOR_LABEL_KIND = "separator"

UNKNOWN_MATERIAL = "UNKNOWN"


@dataclass(frozen=True)
class PrintItem:
    """A single item in a print sequence."""

    kind: str
    file_path: str
    material: str
    board_number: int | None
    is_job_separator: bool = False
    job_name: str = ""


def extract_material_from_filename(
    filename: str,
) -> tuple[str, str, int] | None:
    """Parse ``JOBNAME_MATERIAL_0000.ljd`` into ``(job, material, board_number)``.

    Accepts a path or base filename, any case extension. Returns ``None`` if
    the name does not have at least three underscore-separated segments or
    if the board segment is not numeric.
    """
    base = os.path.basename(filename)
    stem, _ext = os.path.splitext(base)
    if not stem:
        return None

    segments = stem.split("_")
    if len(segments) < 3:
        return None

    board_segment = segments[-1]
    if not board_segment.isdigit():
        return None

    try:
        board_number = int(board_segment)
    except ValueError:
        return None

    material = segments[-2]
    if not material:
        return None

    job_name = "_".join(segments[:-2])
    if not job_name:
        return None

    return job_name, material, board_number


def group_ljd_files_by_material(
    ljd_files: Iterable[str],
) -> dict[str, list[tuple[int, str]]]:
    """Group files by material, preserving original paths.

    Each value is a list of ``(board_number, file_path)`` tuples sorted
    ascending by board number. Files that fail to parse are collected in the
    ``UNKNOWN`` bucket with ``board_number=-1`` so nothing is silently dropped.
    """
    grouped: dict[str, list[tuple[int, str]]] = {}
    for index, path in enumerate(ljd_files):
        parsed = extract_material_from_filename(path)
        if parsed is None:
            grouped.setdefault(UNKNOWN_MATERIAL, []).append((-1 - index, path))
            continue
        _job, material, board = parsed
        grouped.setdefault(material, []).append((board, path))

    for material in grouped:
        grouped[material].sort(key=lambda entry: (entry[0], entry[1]))

    return grouped


def detect_materials_in_job(
    ljd_files: Iterable[str],
    default_priority: tuple[str, ...],
) -> list[tuple[str, int]]:
    """Return per-job detected materials pre-sorted in starting peel order.

    Used by the PrintOrderDialog to seed its draggable stack. The result is a
    list of ``(material, count)`` tuples where the first entry is the
    top-of-roll (peeled first) material.

    The ordering rules, in order:

    1. ``WHMR`` is always pinned to the top of the effective priority if it is
       present in the job and not already listed in ``default_priority``.
    2. Materials that appear in the effective priority are emitted in priority
       order (ones not present in the job are dropped).
    3. Remaining unlisted materials — including the ``UNKNOWN`` bucket — are
       appended in count-descending order with an alphabetical tie-break for
       stability.

    An empty or all-invalid ``ljd_files`` input returns an empty list.
    """
    files_list = list(ljd_files)
    grouped = group_ljd_files_by_material(files_list)
    if not grouped:
        return []

    counts = {material: len(entries) for material, entries in grouped.items()}

    # Build the effective priority: the caller's default, with WHMR pinned at
    # the top as a built-in fallback so a fresh-install user still gets WHMR
    # printed first automatically.
    effective_priority: list[str] = list(default_priority)
    if "WHMR" not in effective_priority:
        effective_priority.insert(0, "WHMR")

    peel_materials = compute_peel_order(
        materials_present=list(grouped.keys()),
        material_priority=tuple(effective_priority),
        material_counts=counts,
    )

    return [(material, counts[material]) for material in peel_materials]


def compute_peel_order(
    materials_present: list[str],
    material_priority: tuple[str, ...],
    material_counts: dict[str, int],
) -> list[str]:
    """Return the peel order of materials.

    Top of the returned list = peeled first. Listed materials come first in
    the order given by ``material_priority``. Unlisted materials come after,
    sorted by count descending with alphabetical tie-break for stability.
    """
    present_set = set(materials_present)
    listed = [m for m in material_priority if m in present_set]
    listed_set = set(listed)

    unlisted = [m for m in materials_present if m not in listed_set]
    unlisted.sort(key=lambda m: (-material_counts.get(m, 0), m))

    return listed + unlisted


def _within_material_peel_order(
    entries: list[tuple[int, str]],
    reverse_within: bool,
) -> list[tuple[int, str]]:
    """Return per-material peel order.

    ``entries`` is sorted ascending by board number. When ``reverse_within``
    is True, peel order is ascending (board 1 first) so the printer will emit
    descending and board 1 ends up on top. When False, peel order is
    descending, which flips the printer output.
    """
    if reverse_within:
        return list(entries)
    return list(reversed(entries))


def build_print_sequence(
    job_name: str,
    ljd_files: Iterable[str],
    material_priority: tuple[str, ...] = (),
    reverse_within: bool = True,
    include_separators: bool = True,
) -> list[PrintItem]:
    """Build the full print sequence for a job.

    Returns a list of :class:`PrintItem` in the exact order the printer will
    emit them. First item = first printed = bottom of stack = last peeled.
    """
    files_list = list(ljd_files)
    grouped = group_ljd_files_by_material(files_list)
    if not grouped:
        return []

    materials_present = list(grouped.keys())
    counts = {m: len(entries) for m, entries in grouped.items()}
    peel_materials = compute_peel_order(materials_present, material_priority, counts)

    peel_items: list[PrintItem] = []
    for material in peel_materials:
        entries = _within_material_peel_order(grouped[material], reverse_within)
        if include_separators:
            is_topmost = material == peel_materials[0]
            peel_items.append(
                PrintItem(
                    kind=SEPARATOR_LABEL_KIND,
                    file_path="",
                    material=material,
                    board_number=None,
                    is_job_separator=is_topmost,
                    job_name=job_name if is_topmost else "",
                )
            )
        for board, path in entries:
            peel_items.append(
                PrintItem(
                    kind=LABEL_KIND,
                    file_path=path,
                    material=material,
                    board_number=board,
                )
            )

    # Print order is the reverse of peel order: the last item peeled is the
    # first one printed (bottom of the stack).
    return list(reversed(peel_items))

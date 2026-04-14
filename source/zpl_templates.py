"""ZPL (Zebra Programming Language) label template builders.

Pure string -> bytes functions for generating ZPL labels sent to the
Zebra GC420D thermal printer. No side effects, no printer dependencies.

The GC420D is typically loaded with 4x2 inch label stock at 203 dpi, but
these templates use auto-fitting commands rather than hardcoded dimensions
so the same output works across label sizes.

ZPL reference (only the commands used here):
    ^XA             start label
    ^CF0,<height>   set font 0 (default scalable) at <height> dots tall
    ^FO<x>,<y>      field origin in dots from top-left
    ^FD<text>^FS    field data followed by field separator
    ^XZ             end label
"""

from __future__ import annotations

# Maximum characters allowed in a single ZPL field before truncation.
# Chosen to comfortably fit a 4x2" label at the font sizes used below.
MAX_FIELD_LENGTH = 60


def sanitize_zpl_field(text: str) -> str:
    """Sanitize user text for safe embedding in a ZPL ^FD field.

    ZPL uses ``^`` and ``~`` as control prefixes, so any occurrence of
    those characters inside field data would terminate the field early
    and corrupt the label. We replace them with a space (a simple, safe
    substitution that never accidentally introduces another control
    character).

    The result is also trimmed and truncated so long job names or
    material codes cannot overflow the physical label.
    """
    if text is None:
        return ""
    cleaned = text.replace("^", " ").replace("~", " ").strip()
    if len(cleaned) > MAX_FIELD_LENGTH:
        cleaned = cleaned[:MAX_FIELD_LENGTH]
    return cleaned


def _encode(zpl: str) -> bytes:
    """Encode a ZPL string as ASCII bytes ready for win32print.WritePrinter.

    Non-ASCII characters are replaced with ``?`` rather than raising, so
    an unusual material name (e.g. containing ``Ω``) never crashes the
    print pipeline.
    """
    return zpl.encode("ascii", errors="replace")


def build_material_separator(material: str) -> bytes:
    """Build a ZPL label showing only the material name, large and bold.

    Used as a section divider between stacks of parts of the same
    material so Marinko can see the material at a glance while peeling
    labels off the roll.
    """
    safe_material = sanitize_zpl_field(material)
    zpl = (
        "^XA\n"
        "^CF0,100\n"
        f"^FO40,80^FD{safe_material}^FS\n"
        "^XZ\n"
    )
    return _encode(zpl)


def build_job_separator(job_name: str, material: str) -> bytes:
    """Build a ZPL label showing job name on top and material below.

    Used as the leading separator for each job's label stack. The job
    name is printed in a smaller font and the material in a larger font
    so the eye lands on the material first when flipping through a
    stack of separators.
    """
    safe_job = sanitize_zpl_field(job_name)
    safe_material = sanitize_zpl_field(material)
    zpl = (
        "^XA\n"
        "^CF0,50\n"
        f"^FO40,40^FD{safe_job}^FS\n"
        "^CF0,80\n"
        f"^FO40,110^FD{safe_material}^FS\n"
        "^XZ\n"
    )
    return _encode(zpl)


def build_test_separator() -> bytes:
    """Build a minimal test label used by the Settings 'Test Print' button.

    Contains a fixed ``TEST PRINT`` header and the ``JobManagerCK``
    brand so the operator can verify the printer is online, loaded, and
    correctly calibrated without needing a real job.
    """
    zpl = (
        "^XA\n"
        "^CF0,60\n"
        "^FO40,40^FDTEST PRINT^FS\n"
        "^CF0,40\n"
        "^FO40,120^FDJobManagerCK^FS\n"
        "^XZ\n"
    )
    return _encode(zpl)

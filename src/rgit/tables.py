# src/rgit/tables.py
from __future__ import annotations
import difflib


def render_table(headers: list[str], rows: list[list[str]],
                 mark: dict[tuple[int, int], bool]) -> str:
    """Render a fixed-width column table. `mark[(r, c)]` appends a ★ to that cell.

    All lines (header + rows) are padded to one common width so the output is a
    clean rectangular block in the terminal.
    """
    cells = [list(headers)] + [list(r) for r in rows]
    marked = [[f"{v} ★" if mark.get((ri - 1, ci)) else v
               for ci, v in enumerate(row)]
              for ri, row in enumerate(cells)]
    widths = [max(len(row[c]) for row in marked) for c in range(len(headers))]
    lines = ["  ".join(v.ljust(widths[c]) for c, v in enumerate(row))
             for row in marked]
    full = max(len(l) for l in lines)
    return "\n".join(l.ljust(full) for l in lines)


def render_diff(clean: str, adapted: str, label: str) -> str:
    """Unified diff clean->adapted under a label; '(identical)' when equal."""
    if clean == adapted:
        return f"{label}: (identical)"
    diff = difflib.unified_diff(
        clean.splitlines(), adapted.splitlines(),
        fromfile="clean", tofile="adapted", lineterm="")
    return label + "\n" + "\n".join(diff)

from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.M)


def _read_python_source(path: Path) -> str:
    """Read a .py file for parsing. ``utf-8-sig`` strips a UTF-8 BOM (common on
    Windows-authored files) that would otherwise make libcst miss the first
    symbol — and it also reads plain UTF-8 unchanged."""
    return path.read_text(encoding="utf-8-sig")


def _changed_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """file -> list of (start, end) line ranges touched on the new side."""
    result: dict[str, list[tuple[int, int]]] = {}
    current: Optional[str] = None
    for line in diff.splitlines():
        m = _FILE.match(line)
        if m:
            current = m.group(1)
            result.setdefault(current, [])
            continue
        h = _HUNK.match(line)
        if h and current:
            start = int(h.group(1))
            length = int(h.group(2) or "1")
            result[current].append((start, start + max(length, 1) - 1))
    return result


class _SymbolFinder(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, ranges: list[tuple[int, int]]):
        self.ranges = ranges
        self.found: set[str] = set()

    def _overlaps(self, node) -> bool:
        pos = self.get_metadata(PositionProvider, node)
        for s, e in self.ranges:
            if pos.start.line <= e and pos.end.line >= s:
                return True
        return False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self._overlaps(node):
            self.found.add(node.name.value)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        if self._overlaps(node):
            self.found.add(node.name.value)


def changed_symbols(diff: str, repo: Path) -> list[dict]:
    """[{file, symbol}] for each top-level def/class overlapping a diff hunk."""
    out: list[dict] = []
    for file, ranges in _changed_line_ranges(diff).items():
        path = repo / file
        if not path.suffix == ".py" or not path.exists() or not ranges:
            continue
        try:
            wrapper = MetadataWrapper(cst.parse_module(_read_python_source(path)))
        except cst.ParserSyntaxError:
            continue
        finder = _SymbolFinder(ranges)
        wrapper.visit(finder)
        for sym in sorted(finder.found):
            out.append({"file": file, "symbol": sym})
    return out


def read_symbol_source(repo: Path, file: str, symbol: str) -> Optional[str]:
    """Current source text of a top-level def/class, or None if absent."""
    path = repo / file
    if not path.exists():
        return None
    try:
        module = cst.parse_module(_read_python_source(path))
    except cst.ParserSyntaxError:
        return None
    for stmt in module.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and stmt.name.value == symbol:
            return module.code_for_node(stmt)
    return None


def symbol_at_line(repo: Path, file: str, line: int) -> Optional[str]:
    """Name of the top-level def/class enclosing `line` (1-based), or None."""
    path = repo / file
    if path.suffix != ".py" or not path.exists():
        return None
    try:
        wrapper = MetadataWrapper(cst.parse_module(_read_python_source(path)))
    except cst.ParserSyntaxError:
        return None
    finder = _SymbolFinder([(line, line)])
    wrapper.visit(finder)
    found = sorted(finder.found)
    return found[0] if found else None

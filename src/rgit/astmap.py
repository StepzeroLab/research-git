from __future__ import annotations
import re
from pathlib import Path
from typing import Callable, Optional

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from .gitutil import parse_git_diff_header, read_worktree_python

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def _changed_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """file -> list of (start, end) ranges of *actually changed* new-side lines.

    Only added lines — plus the new-side anchor of a deletion — count; unified-diff
    context lines are walked to advance the new-side line counter but never recorded.
    Using the whole hunk span (header length) would treat untouched neighbouring
    symbols that merely appear as context as changed (issue #10).
    """
    result: dict[str, list[tuple[int, int]]] = {}
    current: Optional[str] = None
    in_hunk = False
    new_line = 0
    hunk_start = 0
    for line in diff.splitlines():
        matched, path = parse_git_diff_header(line, "+++")
        if matched:
            current = path
            in_hunk = False
            if current is not None:
                result.setdefault(current, [])
            continue
        h = _HUNK.match(line)
        if h:
            new_line = hunk_start = int(h.group(1))
            in_hunk = current is not None
            continue
        if not in_hunk:
            continue
        if not line:                      # empty context line
            new_line += 1
            continue
        tag = line[0]
        if tag == "+":                    # added line -> genuinely changed
            result[current].append((new_line, new_line))
            new_line += 1
        elif tag == "-":                  # deletion -> anchor to the surviving line
            anchor = new_line - 1 if new_line > hunk_start else new_line
            result[current].append((anchor, anchor))
        elif tag == " ":                  # context -> advance, do not record
            new_line += 1
        elif tag == "\\":                 # "\ No newline at end of file"
            continue
        else:                             # non-body line ends the hunk (e.g. next `diff --git`)
            in_hunk = False
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


def changed_symbols(diff: str, repo: Path,
                    read_source: Optional[Callable[[str], Optional[str]]] = None,
                    ) -> list[dict]:
    """[{file, symbol}] for each top-level def/class overlapping a diff hunk.

    `read_source(file)` supplies the new-side source text (None skips the
    file); the default reads the working tree. Committed-diff capture passes a
    reader pinned to the captured commit, so symbol mapping cannot drift when
    the worktree has moved past — or never matched — the diff being segmented
    (e.g. a partially staged commit).
    """
    if read_source is None:
        read_source = lambda file: read_worktree_python(repo, file)  # noqa: E731
    out: list[dict] = []
    for file, ranges in _changed_line_ranges(diff).items():
        if not ranges:
            continue
        text = read_source(file)
        if text is None:
            continue
        try:
            wrapper = MetadataWrapper(cst.parse_module(text))
        except (cst.ParserSyntaxError, UnicodeDecodeError):
            continue
        finder = _SymbolFinder(ranges)
        wrapper.visit(finder)
        for sym in sorted(finder.found):
            out.append({"file": file, "symbol": sym})
    return out


def read_symbol_source(repo: Path, file: str, symbol: str) -> Optional[str]:
    """Current source text of a top-level def/class, or None if absent."""
    text = read_worktree_python(repo, file)
    if text is None:
        return None
    try:
        module = cst.parse_module(text)
    except (cst.ParserSyntaxError, UnicodeDecodeError):
        return None
    for stmt in module.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and stmt.name.value == symbol:
            return module.code_for_node(stmt)
    return None


def symbol_at_line(repo: Path, file: str, line: int) -> Optional[str]:
    """Name of the top-level def/class enclosing `line` (1-based), or None."""
    text = read_worktree_python(repo, file)
    if text is None:
        return None
    try:
        wrapper = MetadataWrapper(cst.parse_module(text))
    except (cst.ParserSyntaxError, UnicodeDecodeError):
        return None
    finder = _SymbolFinder([(line, line)])
    wrapper.visit(finder)
    found = sorted(finder.found)
    return found[0] if found else None

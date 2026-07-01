import os

import pytest

from rgit.astmap import changed_symbols, read_symbol_source, symbol_at_line


def _write_or_skip(path, text: str) -> None:
    try:
        path.write_text(text)
    except OSError as e:
        pytest.skip(f"filesystem does not support this test path: {e}")


def test_changed_symbols_finds_enclosing_function(git_repo):
    src = "def a():\n    return 1\n\ndef b():\n    return 2\n"
    (git_repo / "model.py").write_text(src)
    diff = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n+++ b/model.py\n"
        "@@ -4,2 +4,2 @@ def b():\n-    return 2\n+    return 3\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "model.py", "symbol": "b"} in syms


def test_changed_symbols_handles_diff_header_timestamp_and_spaces(git_repo):
    path = git_repo / "nested dir" / "file with spaces.py"
    path.parent.mkdir()
    path.write_text("def spaced():\n    return 2\n")
    diff = (
        "diff --git a/nested dir/file with spaces.py b/nested dir/file with spaces.py\n"
        "--- a/nested dir/file with spaces.py\n"
        "+++ b/nested dir/file with spaces.py\t2026-01-01\n"
        "@@ -1,2 +1,2 @@\n"
        "-    return 1\n+    return 2\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "nested dir/file with spaces.py", "symbol": "spaced"} in syms


def test_changed_symbols_handles_c_quoted_diff_path(git_repo):
    path = git_repo / "line\nbreak.py"
    _write_or_skip(path, "def odd():\n    return 2\n")
    diff = (
        "diff --git \"a/line\\nbreak.py\" \"b/line\\nbreak.py\"\n"
        "--- \"a/line\\nbreak.py\"\n"
        "+++ \"b/line\\nbreak.py\"\n"
        "@@ -1,2 +1,2 @@\n"
        "-    return 1\n+    return 2\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "line\nbreak.py", "symbol": "odd"} in syms


def test_changed_symbols_does_not_reuse_file_after_dev_null(git_repo):
    (git_repo / "keep.py").write_text("def keep():\n    return 2\n")
    diff = (
        "diff --git a/keep.py b/keep.py\n"
        "--- a/keep.py\n+++ b/keep.py\n"
        "@@ -1,2 +1,2 @@\n-    return 1\n+    return 2\n"
        "diff --git a/gone.py b/gone.py\n"
        "--- a/gone.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def gone():\n-    return 1\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert syms == [{"file": "keep.py", "symbol": "keep"}]


def test_python_symbol_reads_skip_external_symlink(git_repo):
    outside = git_repo.parent / "secret-symbols.py"
    outside.write_text("def TRACKED_SECRET_SYMBOL_TOKEN():\n    return 1\n")
    try:
        os.symlink(outside, git_repo / "linked.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    diff = (
        "diff --git a/linked.py b/linked.py\n"
        "--- a/linked.py\n+++ b/linked.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def linked():\n+    return 1\n"
    )
    assert changed_symbols(diff, git_repo) == []
    assert read_symbol_source(git_repo, "linked.py",
                              "TRACKED_SECRET_SYMBOL_TOKEN") is None
    assert symbol_at_line(git_repo, "linked.py", 1) is None


def test_changed_symbols_handles_utf8_bom(git_repo):
    # A UTF-8 BOM (common on Windows-authored files) must not hide the first symbol.
    (git_repo / "model.py").write_bytes(
        b"\xef\xbb\xbfdef forward(x):\n    return x * 2\n")
    diff = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n+++ b/model.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def forward(x):\n+def forward(x):\n"
        "-    return x\n+    return x * 2\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "model.py", "symbol": "forward"} in syms


def test_read_symbol_source_extracts_function(git_repo):
    (git_repo / "model.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    code = read_symbol_source(git_repo, "model.py", "b")
    assert code.strip().startswith("def b():")
    assert "return 2" in code


def test_changed_symbols_skips_unparseable_file(git_repo):
    from rgit.astmap import changed_symbols
    # a syntactically invalid .py in the working tree must not crash the mapper
    (git_repo / "broken.py").write_text("def loss(x):\n    # only a comment, no body\n")
    diff = ("diff --git a/broken.py b/broken.py\n--- a/broken.py\n+++ b/broken.py\n"
            "@@ -0,0 +1,2 @@\n+def loss(x):\n+    # only a comment, no body\n")
    # should return [] for the unparseable file rather than raising
    assert changed_symbols(diff, git_repo) == []


def test_symbol_at_line_returns_none_for_unparseable_file(git_repo):
    from rgit.astmap import symbol_at_line
    (git_repo / "broken.py").write_text("def loss(x):\n    # only a comment\n")
    assert symbol_at_line(git_repo, "broken.py", 1) is None


def test_read_symbol_source_returns_none_for_unparseable_file(git_repo):
    from rgit.astmap import read_symbol_source
    (git_repo / "broken.py").write_text("def loss(x):\n    # only a comment\n")
    assert read_symbol_source(git_repo, "broken.py", "loss") is None


def test_changed_symbols_excludes_context_only_neighbor(git_repo):
    # issue #10: a neighbouring symbol that only appears as unified-diff context
    # must not be reported as changed.
    src = ("def old_context():\n    return 1\n\n\n"
           "def untouched_neighbor():\n    return 2\n\n\n"
           "def new_feature():\n    return 3\n")
    (git_repo / "m.py").write_text(src)
    diff = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        "@@ -4,3 +4,7 @@ def old_context():\n"
        " \n"
        " def untouched_neighbor():\n"
        "     return 2\n"
        "+\n"
        "+\n"
        "+def new_feature():\n"
        "+    return 3\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "m.py", "symbol": "new_feature"} in syms
    assert {"file": "m.py", "symbol": "untouched_neighbor"} not in syms


def test_changed_symbols_flags_symbol_with_only_deletions(git_repo):
    # a pure deletion inside a surviving function must still flag that function.
    (git_repo / "d.py").write_text("def keep():\n    a = 1\n")
    diff = (
        "diff --git a/d.py b/d.py\n--- a/d.py\n+++ b/d.py\n"
        "@@ -1,3 +1,2 @@\n"
        " def keep():\n"
        "     a = 1\n"
        "-    b = 2\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "d.py", "symbol": "keep"} in syms

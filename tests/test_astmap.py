from rgit.astmap import changed_symbols, read_symbol_source


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

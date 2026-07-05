from rgit.segmenter import HeuristicSegmenter, MockSegmenter, segment_diff
from rgit.store.store import Store


def _skip_unless_git_sees_symlink(repo, rel: str) -> None:
    import subprocess
    import pytest
    raw = subprocess.run(["git", "diff", "--raw", "HEAD", "--", rel], cwd=repo,
                         check=True, capture_output=True).stdout
    if b"120000" not in raw:
        pytest.skip("git does not report symlinks as symlinks on this platform")


def _commit_tracked_symlink_or_skip(repo, rel: str) -> None:
    import subprocess
    import pytest
    subprocess.run(["git", "add", rel], cwd=repo, check=True,
                   capture_output=True)
    mode = subprocess.run(["git", "ls-files", "-s", rel], cwd=repo, check=True,
                          capture_output=True, text=True).stdout
    if not mode.startswith("120000 "):
        pytest.skip("git does not store symlinks as symlinks on this platform")
    subprocess.run(["git", "commit", "-q", "-m", "tracked symlink"],
                   cwd=repo, check=True, capture_output=True)


def test_segment_diff_creates_open_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    candidate = {
        "name": "double-forward", "intent": "scale forward output by 2",
        "code_slices": [{"file": "model.py", "symbol": "forward",
                         "anchor": "L1-L2", "code": "return x * 2", "kind": "wrap"}],
        "knobs": {}, "data_assumptions": None,
        "resurrection_guide": "multiply forward() output by 2", "confidence": 0.9,
    }
    seg = MockSegmenter([candidate])
    pid = segment_diff(store, trigger="manual", segmenter=seg, run_id=None)
    prop = store.get_proposal(pid)
    assert prop.status == "open"
    assert prop.candidates[0]["name"] == "double-forward"
    assert prop.diff_ref  # diff was stored as an object


def test_mock_segmenter_sees_symbol_map(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    seg = MockSegmenter([])
    segment_diff(store, trigger="manual", segmenter=seg, run_id=None)
    assert seg.last_symbols == [{"file": "model.py", "symbol": "forward"}]


def test_segment_diff_skips_empty_diff(git_repo):
    store = Store.init(git_repo)
    seg = MockSegmenter([])
    assert segment_diff(store, trigger="manual", segmenter=seg, run_id=None) is None
    assert store.list_proposals("open") == []


def test_segment_diff_survives_non_utf8_python_file(git_repo):
    store = Store.init(git_repo)
    (git_repo / "latin.py").write_bytes(b"def cafe():\n    return 'caf\xe9'\n")
    pid = segment_diff(store, trigger="manual", segmenter=MockSegmenter([]), run_id=None)
    assert pid is not None


def test_segment_diff_maps_unicode_path_with_quotepath_true(git_repo):
    import subprocess
    store = Store.init(git_repo)
    subprocess.run(["git", "config", "core.quotePath", "true"], cwd=git_repo,
                   check=True)
    (git_repo / "数据处理.py").write_text("def clean():\n    return 1\n")
    pid = segment_diff(store, trigger="manual", segmenter=HeuristicSegmenter(),
                       run_id=None)
    prop = store.get_proposal(pid)
    assert prop.candidates
    assert prop.candidates[0]["code_slices"][0]["file"] == "数据处理.py"


def test_segment_diff_skips_tracked_external_symlink_without_leaking(git_repo):
    import os
    import pytest
    store = Store.init(git_repo)
    outside = git_repo.parent / "secret-target.py"
    outside.write_text("def TRACKED_SECRET_SYMBOL_TOKEN():\n    return 1\n")
    try:
        (git_repo / "model.py").unlink()
        os.symlink(outside, git_repo / "model.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    _skip_unless_git_sees_symlink(git_repo, "model.py")

    pid = segment_diff(store, trigger="manual", segmenter=HeuristicSegmenter(),
                       run_id=None)
    prop = store.get_proposal(pid)
    diff = store.objects.get(prop.diff_ref).decode(errors="replace")
    assert "research-git: skipped tracked file 'model.py'" in diff
    assert "secret-target" not in diff
    assert "TRACKED_SECRET_SYMBOL_TOKEN" not in diff
    assert prop.candidates == []


def test_segment_diff_captures_replaced_external_symlink_without_leaking(git_repo):
    import os
    import subprocess
    import pytest
    store = Store.init(git_repo)
    outside = git_repo.parent / "old-secret-target.py"
    outside.write_text("def OLD_SECRET_SYMBOL_TOKEN():\n    return 1\n")
    try:
        (git_repo / "model.py").unlink()
        os.symlink(outside, git_repo / "model.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    _commit_tracked_symlink_or_skip(git_repo, "model.py")

    (git_repo / "model.py").unlink()
    (git_repo / "model.py").write_text("def replacement():\n    return 2\n")
    pid = segment_diff(store, trigger="manual", segmenter=HeuristicSegmenter(),
                       run_id=None)
    prop = store.get_proposal(pid)
    diff = store.objects.get(prop.diff_ref).decode(errors="replace")
    # the new regular-file content is captured add-only...
    assert "def replacement" in diff
    # ...while the removed external symlink's target never leaks
    assert "old-secret-target" not in diff
    assert "OLD_SECRET_SYMBOL_TOKEN" not in diff


def test_heuristic_segmenter_groups_symbols_per_file():
    diff = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n+++ b/model.py\n"
        "@@ -1,2 +1,2 @@\n-    return x\n+    return x * 2\n"
    )
    symbols = [{"file": "model.py", "symbol": "forward"},
               {"file": "model.py", "symbol": "compute_loss"}]
    cands = HeuristicSegmenter().segment(diff, symbols)
    assert len(cands) == 1                                   # one candidate per file
    assert cands[0]["confidence"] < 0.5                      # flagged as crude/heuristic
    assert {s["symbol"] for s in cands[0]["code_slices"]} == {"forward", "compute_loss"}
    assert all(s["file"] == "model.py" for s in cands[0]["code_slices"])
    assert "return x * 2" in cands[0]["code_slices"][0]["code"]  # carries the diff context


def test_heuristic_segmenter_maps_diff_for_spaced_path():
    diff = (
        "diff --git a/nested dir/file with spaces.py b/nested dir/file with spaces.py\n"
        "--- a/nested dir/file with spaces.py\n"
        "+++ b/nested dir/file with spaces.py\t2026-01-01\n"
        "@@ -1,2 +1,2 @@\n-    return 1\n+    return 2\n"
    )
    symbols = [{"file": "nested dir/file with spaces.py", "symbol": "spaced"}]
    cands = HeuristicSegmenter().segment(diff, symbols)
    assert len(cands) == 1
    assert "return 2" in cands[0]["code_slices"][0]["code"]


def test_heuristic_segmenter_does_not_put_notices_in_code_slice():
    diff = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n+++ b/model.py\n"
        "@@ -1,2 +1,2 @@\n-    return 1\n+    return 2\n"
        "research-git: skipped untracked file 'blob.bin' (binary file)\n"
    )
    symbols = [{"file": "model.py", "symbol": "forward"}]
    cands = HeuristicSegmenter().segment(diff, symbols)
    code = cands[0]["code_slices"][0]["code"]
    assert "return 2" in code
    assert "research-git: skipped" not in code


def test_heuristic_segmenter_no_symbols_yields_no_candidates():
    assert HeuristicSegmenter().segment("", []) == []


def test_segment_diff_records_toggle_event(git_repo):
    import subprocess
    from rgit.segmenter import segment_diff, HeuristicSegmenter
    from rgit.store.store import Store
    from rgit.store.models import Capsule, CodeSlice
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text(
        "def loss(x):\n    return entropy(x)\n    return 0\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "add train"], cwd=git_repo,
                   check=True, capture_output=True)
    fid = store.add_feature(Capsule(
        id="", name="entropy", intent="entropy loss", status="approved",
        base_commit="abc", knobs={}, data_assumptions=None, resurrection_guide="...",
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("train.py", "loss", None, "code", "wrap")]))
    (git_repo / "train.py").write_text(
        "def loss(x):\n    # return entropy(x)\n    return 0\n")
    segment_diff(store, "manual", HeuristicSegmenter(), run_id=None, now="t9")
    latest = store.latest_event(fid)
    assert latest is not None and latest.kind == "deactivate"


def _commit_all(repo, msg):
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True,
                   capture_output=True)


def test_segment_diff_from_commit_source_with_clean_worktree(git_repo):
    from rgit.gitutil import CommitDiffSource, current_commit
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    # worktree is clean: the default worktree source would capture nothing
    assert segment_diff(store, trigger="manual",
                        segmenter=HeuristicSegmenter(), run_id=None) is None
    pid = segment_diff(store, trigger="commit", segmenter=HeuristicSegmenter(),
                       run_id=None, source=CommitDiffSource("HEAD"))
    prop = store.get_proposal(pid)
    assert "x * 2" in store.objects.get(prop.diff_ref).decode()
    assert prop.source_commit == current_commit(git_repo)
    assert prop.candidates and "forward" in prop.candidates[0]["intent"]


def test_segment_diff_commit_source_maps_symbols_from_the_commit(git_repo):
    from rgit.gitutil import CommitDiffSource
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    src = CommitDiffSource("HEAD")
    # the worktree moves on; committed line numbers no longer match it
    (git_repo / "model.py").write_text(
        "# pad\n# pad\n# pad\n# pad\ndef someone_else():\n    return 0\n")
    seg = MockSegmenter([])
    segment_diff(store, trigger="commit", segmenter=seg, run_id=None, source=src)
    assert seg.last_symbols == [{"file": "model.py", "symbol": "forward"}]


def test_segment_diff_dedups_identical_open_diff(git_repo):
    # hook + manual double-fire on the same change must not stack duplicates
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    first = segment_diff(store, "manual", HeuristicSegmenter(), run_id=None)
    again = segment_diff(store, "manual", HeuristicSegmenter(), run_id=None)
    assert again == first
    assert first.created is True
    assert again.created is False
    assert len(store.list_proposals("open")) == 1


def test_segment_diff_resolved_proposal_does_not_block_recapture(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    pid = segment_diff(store, "manual", HeuristicSegmenter(), run_id=None)
    store.set_proposal_status(pid, "dismissed")
    again = segment_diff(store, "manual", HeuristicSegmenter(), run_id=None)
    assert again != pid and again.created is True

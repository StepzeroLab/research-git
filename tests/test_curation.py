import pytest

from rgit.curation import approve, dismiss
from rgit.segmenter import MockSegmenter, segment_diff
from rgit.store.store import Store
from rgit.store.models import Run


def _seed_proposal(store, run_id=None):
    candidate = {
        "name": "double-forward", "intent": "scale forward output by 2",
        "code_slices": [{"file": "model.py", "symbol": "forward",
                         "anchor": "L1", "code": "return x*2", "kind": "wrap"}],
        "knobs": {"factor": 2}, "data_assumptions": None,
        "resurrection_guide": "multiply forward output", "confidence": 0.9,
    }
    return segment_diff(store, "manual", MockSegmenter([candidate]), run_id)


def test_approve_creates_capsule_and_resolves_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    fid = approve(store, pid, candidate_index=0, name="double-forward")
    cap = store.get_feature(fid)
    assert cap.status == "approved"
    assert cap.knobs == {"factor": 2}
    assert store.get_proposal(pid).status == "resolved"


def test_approve_links_feature_to_run(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    rid = store.add_run(Run("", "python train.py", "aa", {"acc": 0.9},
                            "abc", None, "2026-06-16T00:00:00"))
    pid = _seed_proposal(store, run_id=rid)
    fid = approve(store, pid, 0)
    assert store.neighbors(fid, "produced") == [rid]


def test_dismiss_marks_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    dismiss(store, pid)
    assert store.get_proposal(pid).status == "dismissed"


def test_approve_with_lineage_creates_variant_of(git_repo):
    from rgit.store.models import Capsule, CodeSlice
    from rgit.segmenter import segment_diff
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    src = store.add_feature(Capsule(
        id="", name="src", intent="i", status="approved", base_commit="b", knobs={},
        data_assumptions=None, resurrection_guide=None, result_summary=None,
        payload_hash=None, code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")]))
    cand = {"name": "variant", "intent": "tweaked", "code_slices": [
        {"file": "model.py", "symbol": "forward", "anchor": "L1", "code": "x*2", "kind": "wrap"}],
        "knobs": {}, "data_assumptions": None, "resurrection_guide": "g", "confidence": 0.9}
    pid = segment_diff(store, "run", MockSegmenter([cand]), run_id=None, from_features=[src])
    fid = approve(store, pid, 0)
    assert store.neighbors(fid, "variant_of") == [src]


def test_approve_selects_candidate_by_name_ignoring_default_index(git_repo):
    # Two candidates; approving by the SECOND one's name must pick it even though
    # candidate_index defaults to 0 (regression: a forgotten --index silently
    # approved candidate[0] and just relabeled it).
    from rgit.curation import approve
    from rgit.store.store import Store
    from rgit.store.models import Proposal
    store = Store.init(git_repo)
    cands = [
        {"name": "temperature", "intent": "temp", "knobs": {"temperature": 1.5},
         "code_slices": [{"file": "a.py", "symbol": "f", "anchor": None, "code": "x", "kind": "add"}]},
        {"name": "label-smoothing", "intent": "smooth", "knobs": {"smoothing": 0.1},
         "code_slices": [{"file": "b.py", "symbol": "g", "anchor": None, "code": "y", "kind": "add"}]},
    ]
    pid = store.add_proposal(Proposal(id="", trigger="manual", diff_ref="", candidates=cands))
    fid = approve(store, pid, name="label-smoothing")   # no --index passed (default 0)
    cap = store.get_feature(fid)
    assert cap.name == "label-smoothing"
    assert cap.knobs == {"smoothing": 0.1}              # got candidate[1], not [0]


def test_approve_empty_proposal_has_clear_error(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = segment_diff(store, "manual", MockSegmenter([]), run_id=None)
    with pytest.raises(ValueError, match="has no candidates"):
        approve(store, pid)


def test_approve_out_of_range_candidate_has_clear_error(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    with pytest.raises(ValueError, match="candidate index 2 out of range"):
        approve(store, pid, candidate_index=2)


def test_approve_rejects_already_resolved_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    approve(store, pid, 0, name="double-forward")
    before = len(store.list_features())
    with pytest.raises(ValueError):
        approve(store, pid, 0, name="double-forward")     # second approval refused
    assert len(store.list_features()) == before           # no duplicate capsule


def test_approve_with_unmatched_name_fails_instead_of_index_zero(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    with pytest.raises(ValueError):
        approve(store, pid, 0, name="typo-that-matches-nothing")
    assert store.get_proposal(pid).status == "open"        # not silently approved


def test_dismiss_rejects_non_open_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    dismiss(store, pid)
    with pytest.raises(ValueError):
        dismiss(store, pid)


def test_validate_candidates_accepts_wellformed_and_rejects_malformed():
    from rgit.curation import validate_candidates
    validate_candidates([])                                # empty is a valid 0-candidate set
    validate_candidates([{"name": "n", "intent": "i", "code_slices": [
        {"file": "m.py", "symbol": None, "anchor": None, "code": "x", "kind": "add"}]}])
    with pytest.raises(ValueError):
        validate_candidates({"not": "a list"})
    with pytest.raises(ValueError):
        validate_candidates([{"intent": "i", "code_slices": []}])          # missing name
    with pytest.raises(ValueError):
        validate_candidates([{"name": "n", "code_slices": []}])            # missing intent
    with pytest.raises(ValueError):
        validate_candidates([{"name": "n", "intent": "i"}])                # missing code_slices
    with pytest.raises(ValueError):
        validate_candidates([{"name": "n", "intent": "i",
                              "code_slices": [{"file": "m.py"}]}])          # slice missing fields
    with pytest.raises(ValueError):
        validate_candidates([{"name": "n", "intent": "i", "code_slices": [
            {"file": "m.py", "symbol": None, "anchor": None, "code": "x",
             "kind": "add", "bogus": 1}]}])                                 # slice extra field


def test_validate_candidates_rejects_duplicate_names():
    from rgit.curation import validate_candidates
    with pytest.raises(ValueError, match="duplicate candidate name"):
        validate_candidates([
            {"name": "dup", "intent": "first", "code_slices": []},
            {"name": "dup", "intent": "second", "code_slices": []},
        ])


def test_validate_candidates_rejects_comma_in_name():
    from rgit.curation import validate_candidates
    with pytest.raises(ValueError, match="--keep"):
        validate_candidates([
            {"name": "a,b", "intent": "i", "code_slices": []},
        ])


def test_validate_candidates_rejects_untrimmed_name():
    from rgit.curation import validate_candidates
    with pytest.raises(ValueError, match="--keep"):
        validate_candidates([
            {"name": " padded ", "intent": "i", "code_slices": []},
        ])


def test_approve_stamps_base_commit_from_commit_sourced_proposal(git_repo):
    # A committed-diff capture pins the capsule to the commit that contains the
    # change — approving later, after HEAD moved on, must not re-stamp it.
    import subprocess
    from rgit.gitutil import CommitDiffSource, current_commit
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "double"], cwd=git_repo,
                   check=True, capture_output=True)
    captured = current_commit(git_repo)
    candidate = {
        "name": "double-forward", "intent": "scale forward output by 2",
        "code_slices": [{"file": "model.py", "symbol": "forward",
                         "anchor": "L1", "code": "return x*2", "kind": "wrap"}],
        "knobs": {}, "data_assumptions": None,
        "resurrection_guide": "multiply forward output", "confidence": 0.9,
    }
    pid = segment_diff(store, "commit", MockSegmenter([candidate]), None,
                       source=CommitDiffSource("HEAD"))
    (git_repo / "other.py").write_text("OTHER = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "move HEAD"], cwd=git_repo,
                   check=True, capture_output=True)
    fid = approve(store, pid, 0)
    assert store.get_feature(fid).base_commit == captured
    assert captured != current_commit(git_repo)


from rgit.curation import decide


def _seed_multi_proposal(store, run_id=None):
    def cand(name):
        return {
            "name": name, "intent": f"intent of {name}",
            "code_slices": [{"file": "model.py", "symbol": "forward",
                             "anchor": "L1", "code": f"# {name}", "kind": "wrap"}],
            "knobs": {}, "data_assumptions": None,
            "resurrection_guide": f"guide for {name}", "confidence": 0.9,
        }
    return segment_diff(store, "manual",
                        MockSegmenter([cand("rerank"), cand("cache"),
                                       cand("logging")]), run_id)


def test_decide_keeps_multiple_drops_rest_and_resolves(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    rid = store.add_run(Run("", "python train.py", "aa", {"acc": 0.9},
                            "abc", None, "2026-06-16T00:00:00"))
    pid = _seed_multi_proposal(store, run_id=rid)
    approved = decide(store, pid, ["rerank", "cache"])
    assert [n for n, _ in approved] == ["rerank", "cache"]
    assert {c.name for c in store.list_features()} == {"rerank", "cache"}
    for _, fid in approved:
        assert store.neighbors(fid, "produced") == [rid]
        assert store.neighbors(fid, "touches") == ["module:model.py"]
    assert store.get_proposal(pid).status == "resolved"


def test_decide_unknown_name_rejects_whole_call_no_partial_writes(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    with pytest.raises(ValueError, match="typo-name"):
        decide(store, pid, ["rerank", "typo-name"])
    assert store.list_features() == []
    assert store.get_proposal(pid).status == "open"


def test_decide_empty_keep_rejected(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    with pytest.raises(ValueError, match="nothing to keep"):
        decide(store, pid, [])
    assert store.get_proposal(pid).status == "open"


def test_decide_refused_after_resolve(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    decide(store, pid, ["rerank"])
    with pytest.raises(ValueError, match="not open"):
        decide(store, pid, ["cache"])
    assert {c.name for c in store.list_features()} == {"rerank"}


def test_decide_single_name_matches_approve_semantics(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    [(name, fid)] = decide(store, pid, ["double-forward"])
    cap = store.get_feature(fid)
    assert name == "double-forward"
    assert cap.status == "approved"
    assert cap.knobs == {"factor": 2}
    assert store.get_proposal(pid).status == "resolved"


def test_decide_dedupes_repeated_names(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    approved = decide(store, pid, ["rerank", "rerank"])
    assert len(approved) == 1
    assert len(store.list_features()) == 1

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

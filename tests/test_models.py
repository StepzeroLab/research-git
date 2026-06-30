from rgit.store.ids import new_id
from rgit.store.models import Capsule, CodeSlice, ResultSummary, Run


def test_new_id_is_prefixed_and_unique():
    a = new_id("feat_")
    b = new_id("feat_")
    assert a.startswith("feat_") and a != b


def test_capsule_roundtrips_through_dict():
    cap = Capsule(
        id="feat_1", name="contrastive-loss", intent="add aux contrastive loss",
        status="approved", base_commit="abc", knobs={"lambda": 0.1},
        data_assumptions="expects normalized embeddings",
        resurrection_guide="wrap loss in compute_loss; add projection head",
        result_summary=ResultSummary(verdict="improved", key_delta="+1.8 acc",
                                     failure_reason=None, notes=None),
        payload_hash="deadbeef",
        code_slices=[CodeSlice(file="model.py", symbol="compute_loss",
                               anchor="L10-L14", code="loss += ...", kind="insert")],
    )
    assert Capsule.from_dict(cap.to_dict()) == cap


def test_run_roundtrips():
    r = Run(id="run_1", cmd="python train.py", artifact_hash="aa",
            metrics={"acc": 0.9}, base_commit="abc", env={"py": "3.11"},
            created_at="2026-06-16T00:00:00")
    assert Run.from_dict(r.to_dict()) == r

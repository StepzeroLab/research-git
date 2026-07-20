import rgit.mcp_server as srv
from rgit import mcp_server
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(name, origin="live"):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide="reapply", result_summary=None,
                   payload_hash=None,
                   code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")],
                   origin=origin)


def test_recall_tool_returns_serializable_dicts(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.add_feature(_cap("contrastive-loss"))
    out = srv.recall_tool("contrastive")
    assert out[0]["capsule"]["name"] == "contrastive-loss"
    assert "depends_on" in out[0]


def test_compose_tool_returns_brief(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    fid = store.add_feature(_cap("scale"))
    brief = srv.compose_tool([fid])
    assert brief["features"][0]["name"] == "scale"
    assert brief["conflicts"] == []


def test_intelligence_tools_are_not_registered():
    # the write/intelligence-adjacent tools moved to the CLI (plane split)
    assert not hasattr(srv, "pending_captures_tool")
    assert not hasattr(srv, "resegment_tool")


def test_recall_tool_exposes_score_and_overlaps(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = store.add_feature(_cap("alpha"))
    b = store.add_feature(_cap("beta"))
    store.add_edge(a, b, "overlaps")
    out = srv.recall_tool("alpha")
    assert "score" in out[0]
    assert "overlaps" in out[0]


def test_recall_tool_exclude_backfill(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.add_feature(_cap("bf-cache", origin="backfill"))
    hits = srv.recall_tool("cache")
    assert hits and hits[0]["capsule"]["origin"] == "backfill"
    assert srv.recall_tool("cache", exclude_backfill=True) == []


def _cap_v3(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, "c", "wrap")]))


def test_compare_tool_returns_rows(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = _cap_v3(store, "temperature")
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="h",
                            metrics={"eval_loss": 1.1}, base_commit="abc",
                            env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    out = mcp_server.compare_tool("temperature")
    assert out["metric"] == "eval_loss"
    assert out["rows"][0]["feature"] == "temperature"


def test_compare_tool_does_not_write_direction(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = _cap_v3(store, "temperature")
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="h",
                            metrics={"eval_loss": 1.1}, base_commit="abc",
                            env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    mcp_server.compare_tool("temperature")
    # query-only: the tool must not have set a direction
    assert Store.open(git_repo).get_metric_direction("eval_loss") is None

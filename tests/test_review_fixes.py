"""Regression tests for the PR #2 code-review findings (mine + Codex)."""
import io
import sys
import tarfile

import pytest

from rgit.ablation import ablation
from rgit.compare import compare
from rgit.provenance import provenance
from rgit.cli import main
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(store, name, code="c"):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, code, "wrap")]))


def _run(store, metrics, at):
    return store.add_run(Run(id="", cmd="t", artifact_hash="h", metrics=metrics,
                             base_commit="abc", env=None, created_at=at))


# --- store.resolve_feature (shared helper) ----------------------------------

def test_resolve_feature_by_name_and_id(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "temperature")
    assert store.resolve_feature(a) == a              # by id
    assert store.resolve_feature("temperature") == a  # by name
    with pytest.raises(KeyError):
        store.resolve_feature("ghost")


# --- Finding 1: ablation accepts capsule names ------------------------------

def test_ablation_accepts_capsule_names(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "A"); _cap(store, "B")
    r = _run(store, {"eval_loss": 1.0}, "2026-01-01T00:00:00")
    store.add_edge(r, a, "active")
    grid = ablation(store, ["A", "B"])               # names, not ids
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.0


# --- Finding 5: confounded runs are dropped, not folded into a smaller cell --

def test_ablation_drops_run_with_capsule_outside_sweep(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = _cap(store, "A"); b = _cap(store, "B"); c = _cap(store, "C")
    clean = _run(store, {"eval_loss": 1.0}, "2026-01-01T00:00:00")     # active {A}
    store.add_edge(clean, a, "active")
    confounded = _run(store, {"eval_loss": 0.1}, "2026-01-02T00:00:00")  # active {A,C}
    store.add_edge(confounded, a, "active"); store.add_edge(confounded, c, "active")
    grid = ablation(store, [a, b])                    # sweep over {A,B} only
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    # the better-but-confounded {A,C} run must NOT win the +A cell; the clean {A} does
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.0
    assert subsets[("A",)]["run"] == clean


# --- Finding 3: compare uses each capsule's BEST run, not its earliest ------

def test_compare_picks_best_run_not_earliest(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = _cap(store, "temperature")
    r1 = _run(store, {"eval_loss": 1.5}, "2026-01-01T00:00:00")  # earliest, worse
    r2 = _run(store, {"eval_loss": 0.9}, "2026-01-02T00:00:00")  # later, best
    store.add_edge(a, r1, "produced"); store.add_edge(a, r2, "produced")
    row = compare(store, "temperature")["rows"][0]
    assert row["value"] == 0.9 and row["run"] == r2


# --- Finding 6: --higher/--lower overrides for this call only (no DB write) --

def test_compare_direction_override_does_not_persist(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "temperature")
    r = _run(store, {"eval_loss": 1.1}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")
    res = compare(store, "temperature", direction="lower")
    assert res["rows"][0]["winner"] is True            # override honored this call
    assert store.get_metric_direction("eval_loss") is None  # but nothing written


# --- Finding 4: provenance tolerates a non-UTF-8 file in the frozen artifact -

def test_provenance_tolerates_binary_artifact_file(git_repo):
    store = Store.init(git_repo)
    code = "class Loss:\n    pass\n"
    a = _cap(store, "loss", code)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, data in {"loss.py": code.encode(),
                           "weights.bin": b"\x89PNG\xff\xfe\x00\x80"}.items():
            info = tarfile.TarInfo(path); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    h = store.objects.put(buf.getvalue())
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    res = provenance(store, rid)                        # must not raise UnicodeDecodeError
    assert res["slices"][0]["flag"] == "clean"


# --- Finding 2: run --with resolves names; unknown -> clean non-zero exit ----

def test_cli_run_with_unknown_capsule_returns_nonzero(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    rc = main(["run", "--with", "nope", "--", sys.executable, "-c", "pass"])
    assert rc == 1
    assert "no capsule" in capsys.readouterr().out.lower()


def test_cli_run_with_resolves_name_to_active_edge(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = _cap(store, "A")
    rc = main(["run", "--with", "A", "--", sys.executable, "-c", "pass"])
    assert rc == 0
    dsts = [r["dst"] for r in
            store.conn.execute("SELECT dst FROM edges WHERE type='active'")]
    assert dsts == [a]                                  # edge points at the resolved id

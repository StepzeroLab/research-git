# tests/test_provenance.py
import io, tarfile
from rgit.provenance import provenance
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _freeze(store, files: dict[str, str]) -> str:
    """Write a tar artifact {path: text} into the object store; return its hash."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name=path); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return store.objects.put(buf.getvalue())


def _cap(store, name, clean_code):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, clean_code, "wrap")]))


def test_provenance_flags_adapted_when_symbol_differs(git_repo):
    store = Store.init(git_repo)
    clean = "class Loss:\n    pass\n"
    adapted = "class Loss:\n    x = 1\n"
    fid = _cap(store, "loss", clean)
    h = _freeze(store, {"loss.py": adapted})
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(fid, rid, "produced")
    result = provenance(store, rid)
    slice0 = result["slices"][0]
    assert slice0["flag"] == "adapted"
    assert "x = 1" in slice0["diff"]


def test_provenance_flags_clean_when_identical(git_repo):
    store = Store.init(git_repo)
    code = "class Loss:\n    pass\n"
    fid = _cap(store, "loss", code)
    h = _freeze(store, {"loss.py": code})
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(fid, rid, "produced")
    result = provenance(store, rid)
    assert result["slices"][0]["flag"] == "clean"


def test_provenance_flags_missing_when_symbol_absent(git_repo):
    store = Store.init(git_repo)
    fid = _cap(store, "loss", "class Loss:\n    pass\n")
    h = _freeze(store, {"other.py": "x = 1\n"})      # loss.py not in artifact
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(fid, rid, "produced")
    result = provenance(store, rid)
    assert result["slices"][0]["flag"] == "missing"


def test_provenance_unknown_run_raises(git_repo):
    store = Store.init(git_repo)
    import pytest
    with pytest.raises(KeyError):
        provenance(store, "run_nope")

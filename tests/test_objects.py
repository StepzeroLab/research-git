from rgit.store.objects import ObjectStore


def test_put_is_content_addressed_and_roundtrips(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    h1 = store.put(b"hello")
    h2 = store.put(b"hello")
    assert h1 == h2                       # same content -> same hash
    assert len(h1) == 64                  # sha256 hex
    assert store.get(h1) == b"hello"


def test_put_json_roundtrips(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    h = store.put_json({"b": 2, "a": 1})
    assert store.get_json(h) == {"b": 2, "a": 1}

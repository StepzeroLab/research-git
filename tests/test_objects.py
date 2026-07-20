from concurrent.futures import ThreadPoolExecutor
import hashlib
import os

import pytest

import rgit.store.objects as objects_module
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


def test_verify_accepts_matching_object_and_rejects_changed_bytes(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    digest = store.put(b"complete payload")

    assert store.verify(digest) is True

    store.path_for(digest).write_bytes(b"corrupt payload")
    assert store.verify(digest) is False


def test_verify_distinguishes_missing_object(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    digest = "a" * 64

    with pytest.raises(FileNotFoundError):
        store.verify(digest)


@pytest.mark.parametrize("digest", ["", "abc", "A" * 64, "g" * 64, "../escape"])
def test_object_paths_reject_noncanonical_digests(tmp_path, digest):
    store = ObjectStore(tmp_path / "objects")

    assert store.is_valid_digest(digest) is False
    with pytest.raises(ValueError, match="invalid sha256 digest"):
        store.path_for(digest)


def test_put_fsyncs_and_publishes_from_same_directory(tmp_path, monkeypatch):
    store = ObjectStore(tmp_path / "objects")
    data = b"atomic payload"
    digest = hashlib.sha256(data).hexdigest()
    target = store.path_for(digest)
    real_fsync = os.fsync
    real_replace = os.replace
    fsync_calls = []
    replace_calls = []

    def recording_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    def recording_replace(source, destination):
        source = source if hasattr(source, "parent") else type(target)(source)
        destination = (
            destination if hasattr(destination, "parent") else type(target)(destination)
        )
        assert source.parent == target.parent
        assert destination == target
        assert source.read_bytes() == data
        assert not target.exists()
        replace_calls.append((source, destination))
        return real_replace(source, destination)

    monkeypatch.setattr(objects_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(objects_module.os, "replace", recording_replace)

    assert store.put(data) == digest
    assert fsync_calls
    assert len(replace_calls) == 1
    assert target.read_bytes() == data


def test_failed_publish_leaves_no_object_or_temp_file(tmp_path, monkeypatch):
    store = ObjectStore(tmp_path / "objects")
    data = b"interrupted payload"
    digest = hashlib.sha256(data).hexdigest()
    target = store.path_for(digest)

    def fail_replace(source, destination):
        raise OSError("simulated publish failure")

    monkeypatch.setattr(objects_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated publish failure"):
        store.put(data)

    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_put_does_not_replace_valid_existing_object(tmp_path, monkeypatch):
    store = ObjectStore(tmp_path / "objects")
    data = b"existing payload"
    digest = store.put(data)

    def unexpected_replace(source, destination):
        pytest.fail("valid object should not be replaced")

    monkeypatch.setattr(objects_module.os, "replace", unexpected_replace)

    assert store.put(data) == digest
    assert store.get(digest) == data


def test_put_repairs_corrupt_existing_object(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    data = b"complete payload"
    digest = store.put(data)
    target = store.path_for(digest)
    target.write_bytes(b"partial")

    assert store.verify(digest) is False
    assert store.put(data) == digest
    assert store.get(digest) == data
    assert store.verify(digest) is True


def test_put_accepts_valid_object_published_by_concurrent_writer(
    tmp_path, monkeypatch
):
    store = ObjectStore(tmp_path / "objects")
    data = b"racing payload"
    digest = hashlib.sha256(data).hexdigest()
    target = store.path_for(digest)

    def concurrent_publish_then_fail(source, destination):
        target.write_bytes(data)
        raise PermissionError("target was published by another writer")

    monkeypatch.setattr(
        objects_module.os,
        "replace",
        concurrent_publish_then_fail,
    )

    assert store.put(data) == digest
    assert store.verify(digest) is True
    assert not list(target.parent.glob("*.tmp"))


def test_put_retries_transient_verify_permission_error(tmp_path, monkeypatch):
    store = ObjectStore(tmp_path / "objects")
    data = b"temporarily locked payload"
    digest = store.put(data)
    real_verify = store.verify
    attempts = 0
    delays = []

    def locked_once(candidate):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("simulated Windows sharing violation")
        return real_verify(candidate)

    monkeypatch.setattr(store, "verify", locked_once)
    monkeypatch.setattr(objects_module.time, "sleep", delays.append)

    assert store.put(data) == digest
    assert attempts == 2
    assert delays == [store._ACCESS_RETRY_DELAYS[0]]


def test_concurrent_puts_publish_one_valid_object(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    data = b"concurrent payload" * 1024
    expected = hashlib.sha256(data).hexdigest()

    with ThreadPoolExecutor(max_workers=16) as pool:
        digests = list(pool.map(lambda _: store.put(data), range(64)))

    assert set(digests) == {expected}
    assert store.verify(expected) is True
    assert store.get(expected) == data

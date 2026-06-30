# src/rgit/provenance.py
from __future__ import annotations
import io
import tarfile
from typing import Optional

import libcst as cst

from .store.store import Store
from .tables import render_diff


def _symbol_from_text(text: str, symbol: str) -> Optional[str]:
    """Source of a top-level def/class in `text`, or None (mirrors astmap)."""
    try:
        module = cst.parse_module(text)
    except cst.ParserSyntaxError:
        return None
    for stmt in module.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and stmt.name.value == symbol:
            return module.code_for_node(stmt)
    return None


def _artifact_files(store: Store, artifact_hash: str) -> dict[str, bytes]:
    """Untar a frozen artifact in memory -> {path: raw bytes}.

    Bytes (not text): the freeze archives the whole worktree, so it may contain
    binary blobs (images, checkpoints, datasets). Decoding happens lazily, only
    for the slice files we actually read, so an unrelated binary member can never
    blow up the whole audit with a UnicodeDecodeError.
    """
    blob = store.objects.get(artifact_hash)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        for member in tar.getmembers():
            f = tar.extractfile(member)
            if f is not None:
                files[member.name] = f.read()
    return files


def _file_text(files: dict[str, bytes], path: str) -> Optional[str]:
    """Decode one artifact file as UTF-8 text, or None if absent / not text."""
    raw = files.get(path)
    if raw is None:
        return None
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return None


def _capsules_for_run(store: Store, run_id: str) -> list[str]:
    produced = [r["src"] for r in store.conn.execute(
        "SELECT src FROM edges WHERE dst=? AND type=?", (run_id, "produced"))]
    active = store.active_features(run_id)
    seen, out = set(), []
    for fid in produced + active:
        if fid not in seen:
            seen.add(fid); out.append(fid)
    return out


def provenance(store: Store, run_id: str) -> dict:
    """Per-slice clean (capsule) vs adapted (frozen artifact) audit.

    Each slice flag is 'clean' (identical after trailing-whitespace normalization),
    'adapted' (differs, with a diff), or 'missing' (symbol/file absent from the
    run's artifact).
    """
    run = store.get_run(run_id)                 # raises KeyError on unknown run
    files = _artifact_files(store, run.artifact_hash)
    slices = []
    for fid in _capsules_for_run(store, run_id):
        cap = store.get_feature(fid)
        for s in cap.code_slices:
            if not s.symbol:
                continue
            adapted = _symbol_from_text(_file_text(files, s.file) or "", s.symbol)
            label = f"{cap.name}  {s.file}:{s.symbol}"
            if adapted is None:
                slices.append({"feature": cap.name, "symbol": s.symbol,
                               "flag": "missing", "diff": ""})
            elif adapted.rstrip() == s.code.rstrip():
                slices.append({"feature": cap.name, "symbol": s.symbol,
                               "flag": "clean", "diff": ""})
            else:
                slices.append({"feature": cap.name, "symbol": s.symbol,
                               "flag": "adapted",
                               "diff": render_diff(s.code, adapted, label)})
    counts = {"clean": 0, "adapted": 0, "missing": 0}
    for sl in slices:
        counts[sl["flag"]] += 1
    return {"run": run_id, "slices": slices, "summary": counts}

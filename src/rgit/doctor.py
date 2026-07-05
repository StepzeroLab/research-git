from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .curation import validate_candidates
from .store.db import SCHEMA_VERSION
from .store.models import CAPSULE_EDGE_TYPES, SYMMETRIC_EDGE_TYPES, CodeSlice
from .store.store import Store


def open_doctor_store(start: Path | None = None) -> Store:
    """A store the doctor can only observe: same discovery and layout as every
    other command (via Store), but read-only — no migrations, no repairs."""
    return Store.open(start, readonly=True)


def run_doctor(store) -> dict[str, Any]:
    """Inspect a research-git store without relying on higher-level decoders."""
    findings: list[dict[str, Any]] = []

    feature_ids = _ids(store, "features")
    run_ids = _ids(store, "runs")

    schema_version = _schema_version(store, findings)
    _check_feature_payloads(store, findings)
    _check_run_artifacts(store, findings)
    _check_proposals(store, findings)
    _check_edges(store, findings, feature_ids, run_ids)

    errors = sum(1 for f in findings if f["level"] == "error")
    warnings = sum(1 for f in findings if f["level"] == "warning")
    return {
        "ok": errors == 0,
        "schema": {
            "version": schema_version,
            "expected_version": SCHEMA_VERSION,
        },
        "summary": {
            "errors": errors,
            "warnings": warnings,
        },
        "findings": findings,
    }


def error_report(code: str, message: str, subject: str) -> dict[str, Any]:
    finding = {
        "level": "error",
        "code": code,
        "message": message,
        "subject": subject,
    }
    return {
        "ok": False,
        "schema": {
            "version": None,
            "expected_version": SCHEMA_VERSION,
        },
        "summary": {
            "errors": 1,
            "warnings": 0,
        },
        "findings": [finding],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "research-git doctor",
        f"schema: {report['schema'].get('version') or 'missing'} "
        f"(expected {report['schema']['expected_version']})",
        f"errors: {report['summary']['errors']}",
        f"warnings: {report['summary']['warnings']}",
    ]
    if not report["findings"]:
        lines.append("ok: no findings")
        return "\n".join(lines)

    for finding in report["findings"]:
        subject = finding.get("subject")
        suffix = f" [{subject}]" if subject else ""
        lines.append(
            f"{finding['level']}: {finding['code']}{suffix}: "
            f"{finding['message']}"
        )
    return "\n".join(lines)


def _ids(store, table: str) -> set[str]:
    return {
        row["id"]
        for row in store.conn.execute(f"SELECT id FROM {table}")
    }


def _schema_version(store, findings: list[dict[str, Any]]) -> str | None:
    table = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
    ).fetchone()
    if table is None:
        _add(
            findings,
            "warning",
            "missing_schema_metadata",
            "schema metadata table is missing",
            "schema_metadata",
        )
        return None
    row = store.conn.execute(
        "SELECT value FROM schema_metadata WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        _add(
            findings,
            "warning",
            "missing_schema_version",
            "schema_version metadata row is missing",
            "schema_metadata.schema_version",
        )
        return None
    version = row["value"]
    if version != SCHEMA_VERSION:
        _add(
            findings,
            "warning",
            "schema_version_mismatch",
            f"store schema_version is {version}, expected {SCHEMA_VERSION}",
            "schema_metadata.schema_version",
        )
    return version


def _check_feature_payloads(store, findings: list[dict[str, Any]]) -> None:
    for row in store.conn.execute("SELECT id, payload_hash FROM features"):
        fid = row["id"]
        digest = row["payload_hash"]
        if not digest:
            _add(
                findings,
                "error",
                "missing_feature_payload_hash",
                "feature has no payload_hash",
                fid,
            )
            continue
        try:
            payload = _load_json_object(store, digest)
        except FileNotFoundError:
            _add(
                findings,
                "error",
                "missing_feature_payload_object",
                "feature payload_hash does not resolve to an object",
                fid,
            )
            continue
        except (UnicodeDecodeError, json.JSONDecodeError):
            _add(
                findings,
                "error",
                "malformed_feature_payload_json",
                "feature payload object is not valid JSON",
                fid,
            )
            continue
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            _add(
                findings,
                "error",
                "malformed_feature_payload_json",
                "feature payload object must decode as a JSON list of code slices",
                fid,
            )
            continue
        for item in payload:
            try:
                CodeSlice(**item)
            except TypeError:
                _add(
                    findings,
                    "error",
                    "malformed_feature_payload_json",
                    "feature payload contains malformed code slice objects",
                    fid,
                )
                break


def _check_run_artifacts(store, findings: list[dict[str, Any]]) -> None:
    for row in store.conn.execute("SELECT id, artifact_hash FROM runs"):
        rid = row["id"]
        digest = row["artifact_hash"]
        if not digest:
            _add(
                findings,
                "error",
                "missing_run_artifact_hash",
                "run has no artifact_hash",
                rid,
            )
            continue
        if not store.objects.path_for(digest).exists():
            _add(
                findings,
                "error",
                "missing_run_artifact_object",
                "run artifact_hash does not resolve to an object",
                rid,
            )


def _check_proposals(store, findings: list[dict[str, Any]]) -> None:
    for row in store.conn.execute("SELECT id, diff_ref, candidates FROM proposals"):
        pid = row["id"]
        diff_ref = row["diff_ref"]
        if not diff_ref:
            _add(
                findings,
                "error",
                "missing_proposal_diff_ref",
                "proposal has no diff_ref",
                pid,
            )
        elif not store.objects.path_for(diff_ref).exists():
            _add(
                findings,
                "error",
                "missing_proposal_diff_object",
                "proposal diff_ref does not resolve to an object",
                pid,
            )
        try:
            candidates = json.loads(row["candidates"])
        except json.JSONDecodeError:
            _add(
                findings,
                "error",
                "malformed_proposal_candidates_json",
                "proposal candidates column is not valid JSON",
                pid,
            )
            continue
        if not isinstance(candidates, list):
            _add(
                findings,
                "error",
                "malformed_proposal_candidates_json",
                "proposal candidates column must decode as a JSON list",
                pid,
            )
            continue
        try:
            validate_candidates(candidates)
        except ValueError as e:
            _add(
                findings,
                "error",
                "malformed_proposal_candidates_json",
                f"proposal candidates are structurally invalid: {e}",
                pid,
            )


def _check_edges(
    store,
    findings: list[dict[str, Any]],
    feature_ids: set[str],
    run_ids: set[str],
) -> None:
    edges = {
        (row["src"], row["dst"], row["type"])
        for row in store.conn.execute("SELECT src, dst, type FROM edges")
    }
    for src, dst, edge_type in sorted(edges):
        _check_edge_endpoint(findings, src, feature_ids, run_ids, edge_type, "src")
        _check_edge_endpoint(findings, dst, feature_ids, run_ids, edge_type, "dst")
        if edge_type in SYMMETRIC_EDGE_TYPES and (dst, src, edge_type) not in edges:
            _add(
                findings,
                "warning",
                "missing_reverse_edge",
                f"{edge_type} should also exist from {dst} to {src}",
                f"{src}->{dst}:{edge_type}",
            )


def _check_edge_endpoint(
    findings: list[dict[str, Any]],
    value: str,
    feature_ids: set[str],
    run_ids: set[str],
    edge_type: str,
    side: str,
) -> None:
    expected = _expected_endpoint(edge_type, side)
    if expected == "feature" and value not in feature_ids:
        _add(
            findings,
            "error",
            "dangling_edge",
            f"{edge_type} {side} endpoint {value} does not resolve to a feature",
            f"{edge_type}:{side}:{value}",
        )
    elif expected == "run" and value not in run_ids:
        _add(
            findings,
            "error",
            "dangling_edge",
            f"{edge_type} {side} endpoint {value} does not resolve to a run",
            f"{edge_type}:{side}:{value}",
        )
    elif expected == "module" and not value.startswith("module:"):
        _add(
            findings,
            "error",
            "dangling_edge",
            f"{edge_type} {side} endpoint {value} is not a module reference",
            f"{edge_type}:{side}:{value}",
        )
    elif expected == "known" and value not in feature_ids and value not in run_ids:
        _add(
            findings,
            "warning",
            "unknown_edge_endpoint",
            f"{edge_type} {side} endpoint {value} does not resolve to a feature or run",
            f"{edge_type}:{side}:{value}",
        )


def _expected_endpoint(edge_type: str, side: str) -> str:
    if edge_type == "touches":
        return "feature" if side == "src" else "module"
    if edge_type == "produced":
        return "feature" if side == "src" else "run"
    if edge_type == "active":
        return "run" if side == "src" else "feature"
    if edge_type in CAPSULE_EDGE_TYPES:
        return "feature"
    return "known"


def _load_json_object(store, digest: str) -> Any:
    return json.loads(store.objects.get(digest))


def _add(
    findings: list[dict[str, Any]],
    level: str,
    code: str,
    message: str,
    subject: str,
) -> None:
    findings.append({
        "level": level,
        "code": code,
        "message": message,
        "subject": subject,
    })

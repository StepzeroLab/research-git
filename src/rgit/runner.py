from __future__ import annotations
from dataclasses import dataclass
import os
import subprocess
from typing import Optional

from .gitutil import current_commit, freeze_worktree
from .metrics import parse_metrics
from .segmenter import Segmenter, segment_diff
from .store.models import Run
from .store.store import Store


COMMAND_NOT_FOUND = 127


@dataclass
class ExperimentResult:
    run_id: str
    proposal_id: Optional[str]
    returncode: int
    stdout: str
    stderr: str
    metrics: Optional[dict]

    def __iter__(self):
        # Backward-compatible unpacking for callers that expect (run_id, prop_id).
        yield self.run_id
        yield self.proposal_id


def run_experiment(store: Store, cmd: list[str], segmenter: Segmenter,
                   now: str, env: Optional[dict] = None,
                   from_features: Optional[list[str]] = None,
                   active: Optional[list[str]] = None) -> ExperimentResult:
    """Execute an experiment, freeze the artifact, record the run, segment the diff.

    Returns (run_id, proposal_id). `now` is an ISO timestamp injected by the
    caller (keeps the function deterministic for tests). `from_features` marks this
    run as a regeneration of those capsule(s): each gets a `produced` edge to the
    new run (immediate results link), and the proposal carries the lineage so
    approval establishes `variant_of`.

    `active` declares which approved capsules were active in the working tree for
    this run; each gets a `run -active-> capsule` edge so `rgit ablation` can group
    runs by their active-feature set.
    """
    base = current_commit(store.root)
    # `env` overlays the parent environment (not a replacement) so the child still
    # inherits PATH etc. — and so the recorded env actually reaches the process.
    run_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.run(cmd, cwd=store.root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=run_env)
        returncode = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except FileNotFoundError as e:
        returncode = COMMAND_NOT_FOUND
        stdout = ""
        stderr = f"command not found: {cmd[0]}"
        proc = None
    # Freeze BEFORE parsing metrics: the compute is already spent, so nothing
    # downstream (a bad metric line, etc.) may cost us the reproducible artifact.
    artifact = freeze_worktree(store.root, store.objects)
    metrics = parse_metrics(stdout, store.root)
    run_id = store.add_run(Run(
        id="", cmd=" ".join(cmd), artifact_hash=artifact, metrics=metrics,
        base_commit=base, env=env, created_at=now, returncode=returncode))
    for src in (from_features or []):
        store.add_edge(src, run_id, "produced")     # source capsule's lineage -> this run
    for cap_id in (active or []):
        store.add_edge(run_id, cap_id, "active")    # this run -> active capsule
    prop_id = segment_diff(store, trigger="run", segmenter=segmenter, run_id=run_id,
                           from_features=from_features, now=now)
    return ExperimentResult(run_id=run_id, proposal_id=prop_id,
                            returncode=returncode, stdout=stdout,
                            stderr=stderr, metrics=metrics)

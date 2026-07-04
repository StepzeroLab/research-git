from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .ids import new_id
from .models import Capsule, Run, Proposal, Event
from .objects import ObjectStore


class Store:
    """Facade over the graph DB and object store under <root>/.rgit/."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / ".rgit"
        self.objects = ObjectStore(self.dir / "objects")
        self.conn = connect(self.dir / "graph.db")
        init_schema(self.conn)   # idempotent: ensures schema + migrations on every open

    @classmethod
    def init(cls, root: Path) -> "Store":
        return cls(root)

    @classmethod
    def open(cls, start: Optional[Path] = None) -> "Store":
        cur = Path(start or Path.cwd()).resolve()
        for cand in [cur, *cur.parents]:
            if (cand / ".rgit").is_dir():
                return cls(cand)
        raise FileNotFoundError("no .rgit/ found (run `rgit init`)")

    # ---- features -----------------------------------------------------
    def add_feature(self, cap: Capsule) -> str:
        fid = cap.id or new_id("feat_")
        payload = [c.__dict__ for c in cap.code_slices]
        payload_hash = self.objects.put_json(payload)
        rs = json.dumps(cap.result_summary.__dict__) if cap.result_summary else None
        self.conn.execute(
            "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fid, cap.name, cap.intent, cap.status, cap.base_commit,
             json.dumps(cap.knobs), cap.data_assumptions, cap.resurrection_guide,
             rs, payload_hash))
        self.conn.commit()
        return fid

    def _row_to_capsule(self, row) -> Capsule:
        from .models import CodeSlice, ResultSummary
        slices = [CodeSlice(**c) for c in self.objects.get_json(row["payload_hash"])]
        rs = json.loads(row["result_summary"]) if row["result_summary"] else None
        return Capsule(
            id=row["id"], name=row["name"], intent=row["intent"],
            status=row["status"], base_commit=row["base_commit"],
            knobs=json.loads(row["knobs"]), data_assumptions=row["data_assumptions"],
            resurrection_guide=row["resurrection_guide"],
            result_summary=ResultSummary(**rs) if rs else None,
            payload_hash=row["payload_hash"], code_slices=slices)

    def get_feature(self, fid: str) -> Capsule:
        row = self.conn.execute("SELECT * FROM features WHERE id=?", (fid,)).fetchone()
        if row is None:
            raise KeyError(fid)
        return self._row_to_capsule(row)

    def list_features(self) -> list[Capsule]:
        rows = self.conn.execute("SELECT * FROM features").fetchall()
        return [self._row_to_capsule(r) for r in rows]

    def resolve_feature(self, token: str) -> str:
        """Resolve a capsule id or name to its id; raise KeyError if neither.

        Lets user-facing commands accept the friendly capsule *name* (what the
        docs show) as well as the internal id, with id taking precedence.
        """
        feats = self.list_features()
        for f in feats:
            if f.id == token:
                return f.id
        for f in feats:
            if f.name == token:
                return f.id
        raise KeyError(f"no capsule matching '{token}'")

    def update_capsule(self, fid: str, *, resurrection_guide: Optional[str] = None,
                       result_summary=None) -> None:
        """Refresh mutable fields of an approved capsule (the 'capsule learns' path)."""
        sets, vals = [], []
        if resurrection_guide is not None:
            sets.append("resurrection_guide=?")
            vals.append(resurrection_guide)
        if result_summary is not None:
            sets.append("result_summary=?")
            rs = result_summary.__dict__ if hasattr(result_summary, "__dict__") else result_summary
            vals.append(json.dumps(rs))
        if not sets:
            return
        vals.append(fid)
        self.conn.execute(f"UPDATE features SET {', '.join(sets)} WHERE id=?", vals)
        self.conn.commit()

    # ---- edges --------------------------------------------------------
    def add_edge(self, src: str, dst: str, type: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO edges VALUES (?,?,?)", (src, dst, type))
        self.conn.commit()

    def neighbors(self, src: str, type: str) -> list[str]:
        return [r["dst"] for r in self.conn.execute(
            "SELECT dst FROM edges WHERE src=? AND type=?", (src, type))]

    def active_features(self, run_id: str) -> list[str]:
        """Capsules declared active in a run (run -active-> capsule edges)."""
        return self.neighbors(run_id, "active")

    def runs_with_active(self, capsule_id: str) -> list[str]:
        """Runs that declared this capsule active (incoming active edges)."""
        return [r["src"] for r in self.conn.execute(
            "SELECT src FROM edges WHERE dst=? AND type=?", (capsule_id, "active"))]

    # ---- runs ---------------------------------------------------------
    def add_run(self, run: Run) -> str:
        rid = run.id or new_id("run_")
        self.conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (rid, run.cmd, run.artifact_hash,
             json.dumps(run.metrics) if run.metrics is not None else None,
             run.base_commit, json.dumps(run.env) if run.env else None,
             run.created_at, run.returncode))
        self.conn.commit()
        return rid

    def get_run(self, rid: str) -> Run:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return Run(id=row["id"], cmd=row["cmd"], artifact_hash=row["artifact_hash"],
                   metrics=json.loads(row["metrics"]) if row["metrics"] else None,
                   base_commit=row["base_commit"],
                   env=json.loads(row["env"]) if row["env"] else None,
                   created_at=row["created_at"], returncode=row["returncode"])

    # ---- proposals ----------------------------------------------------
    def add_proposal(self, p: Proposal) -> str:
        pid = p.id or new_id("prop_")
        self.conn.execute(
            "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?)",
            (pid, p.trigger, p.diff_ref, json.dumps(p.candidates), p.status, p.run_id,
             json.dumps(p.from_features) if p.from_features else None,
             p.source_commit))
        self.conn.commit()
        return pid

    def get_proposal(self, pid: str) -> Proposal:
        row = self.conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(pid)
        return Proposal(id=row["id"], trigger=row["trigger"], diff_ref=row["diff_ref"],
                        candidates=json.loads(row["candidates"]), status=row["status"],
                        run_id=row["run_id"],
                        from_features=json.loads(row["from_features"])
                        if row["from_features"] else None,
                        source_commit=row["source_commit"])

    def list_proposals(self, status: Optional[str] = None) -> list[Proposal]:
        if status:
            rows = self.conn.execute(
                "SELECT id FROM proposals WHERE status=?", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT id FROM proposals").fetchall()
        return [self.get_proposal(r["id"]) for r in rows]

    def set_proposal_status(self, pid: str, status: str) -> None:
        cur = self.conn.execute("UPDATE proposals SET status=? WHERE id=?",
                                (status, pid))
        if cur.rowcount == 0:                       # unknown id must not look like success
            raise KeyError(pid)
        self.conn.commit()

    def set_proposal_candidates(self, pid: str, candidates: list[dict]) -> None:
        """Replace a proposal's candidate list (used by host-agent re-segmentation)."""
        cur = self.conn.execute("UPDATE proposals SET candidates=? WHERE id=?",
                                (json.dumps(candidates), pid))
        if cur.rowcount == 0:
            raise KeyError(pid)
        self.conn.commit()

    # ---- events -------------------------------------------------------
    def add_event(self, capsule_id: str, kind: str, run_id: Optional[str],
                  created_at: str) -> str:
        eid = new_id("evt_")
        self.conn.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                          (eid, capsule_id, kind, run_id, created_at))
        self.conn.commit()
        return eid

    def latest_event(self, capsule_id: str) -> Optional[Event]:
        row = self.conn.execute(
            "SELECT * FROM events WHERE capsule_id=? ORDER BY created_at DESC, rowid DESC "
            "LIMIT 1", (capsule_id,)).fetchone()
        if row is None:
            return None
        return Event(id=row["id"], capsule_id=row["capsule_id"], kind=row["kind"],
                     run_id=row["run_id"], created_at=row["created_at"])

    # ---- metric directions -------------------------------------------
    def set_metric_direction(self, metric: str, direction: str) -> None:
        """Record whether a metric is better when 'higher' or 'lower' (upsert)."""
        self.conn.execute(
            "INSERT INTO metric_directions VALUES (?,?) "
            "ON CONFLICT(metric) DO UPDATE SET direction=excluded.direction",
            (metric, direction))
        self.conn.commit()

    def get_metric_direction(self, metric: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT direction FROM metric_directions WHERE metric=?",
            (metric,)).fetchone()
        return row["direction"] if row else None

    def list_metric_directions(self) -> dict[str, str]:
        return {r["metric"]: r["direction"] for r in
                self.conn.execute("SELECT metric, direction FROM metric_directions")}

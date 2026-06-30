from __future__ import annotations
import argparse
import datetime
import json
import sys
from typing import Optional

GUIDANCE_MODES = ("default", "manual-only", "none")


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _prompt_guidance_mode(platform: str) -> str:
    """Interactive picker shown only on a TTY when --guidance was not passed.

    Prompts go to stderr so stdout stays a clean JSON document.
    """
    sys.stderr.write(
        f"\nresearch-git guidance for {platform} "
        "— how proactive should capture be?\n"
        "  1) default     — consider capture after meaningful changes (recommended)\n"
        "  2) manual-only — only when you explicitly ask\n"
        "  3) none        — install skills + MCP only, write no guidance\n"
    )
    choices = {"1": "default", "2": "manual-only", "3": "none", "": "default",
               "default": "default", "manual-only": "manual-only", "none": "none"}
    while True:
        sys.stderr.write("> ")
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError:
            return "default"
        if answer in choices:
            return choices[answer]
        sys.stderr.write("Please enter 1, 2, or 3.\n")

from .curation import approve, dismiss
from .runner import run_experiment
from .segmenter import Segmenter, segment_diff
from .store.store import Store

# Test seam: when set, used instead of the default free HeuristicSegmenter.
_SEGMENTER: Optional[Segmenter] = None


def _segmenter() -> Segmenter:
    if _SEGMENTER is not None:
        return _SEGMENTER
    from .segmenter import HeuristicSegmenter
    return HeuristicSegmenter()


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rgit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p_run = sub.add_parser("run")
    p_run.add_argument("--from", dest="from_features", action="append",
                       metavar="CAPSULE_ID",
                       help="mark this run as a regeneration of CAPSULE_ID (repeatable)")
    p_run.add_argument("--refresh-guide-file", metavar="PATH",
                       help="refresh the --from capsule(s) resurrection_guide from this file")
    p_run.add_argument("--with", dest="active", action="append", metavar="CAPSULE",
                       help="declare capsule(s) active in this run, by name or id; "
                            "repeatable and/or comma-separated")
    p_run.add_argument("--init", action="store_true",
                       help="create .rgit/ at the git root if missing (no hooks)")
    p_run.add_argument("rest", nargs=argparse.REMAINDER)  # after `--`

    p_cap = sub.add_parser("capture")
    p_cap.add_argument("--trigger", default="manual")
    p_cap.add_argument("--init", action="store_true",
                       help="create .rgit/ at the git root if missing (no hooks)")

    p_rev = sub.add_parser("review")
    p_rev.add_argument("--approve")
    p_rev.add_argument("--name")
    p_rev.add_argument("--index", type=int, default=0)
    p_rev.add_argument("--dismiss")

    sub.add_parser("features")
    sub.add_parser("mcp")          # run the MCP server (the query/share surface)

    p_edges = sub.add_parser("edges")
    p_edges.add_argument("--apply", action="store_true")
    p_edges.add_argument("--candidates", action="store_true")
    p_edges.add_argument("--add", nargs=3, metavar=("TYPE", "SRC", "DST"))

    p_pend = sub.add_parser("pending")
    p_pend.add_argument("--json", action="store_true")

    p_reseg = sub.add_parser("resegment")
    p_reseg.add_argument("proposal_id")
    p_reseg.add_argument("--from-json", dest="from_json", required=True,
                         metavar="PATH", help="file path, or - for stdin")

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--interval", type=float, default=5.0)
    p_watch.add_argument("--idle", type=float, default=5.0)
    p_watch.add_argument("--once", action="store_true")

    p_inst = sub.add_parser("install")   # wire plugin + MCP into an AI client
    p_inst.add_argument("platform", nargs="?")
    p_inst.add_argument("--list", action="store_true")
    p_inst.add_argument("--uninstall", action="store_true")
    p_inst.add_argument("--dry-run", action="store_true")
    p_inst.add_argument("--guidance", choices=list(GUIDANCE_MODES),
                        help="guidance to write: default | manual-only | none "
                             "(none = skills + MCP only). If omitted, you are "
                             "asked interactively on a TTY, else 'default' is kept.")
    p_inst.add_argument("--scope", default="user", choices=["user", "project", "local"])

    p_ih = sub.add_parser("install-hooks")   # git post-commit capture hook
    p_ih.add_argument("--uninstall", action="store_true")
    p_ih.add_argument("--dry-run", action="store_true")

    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("target")
    p_cmp.add_argument("--metric")
    dgrp = p_cmp.add_mutually_exclusive_group()
    dgrp.add_argument("--higher", dest="direction", action="store_const", const="higher")
    dgrp.add_argument("--lower", dest="direction", action="store_const", const="lower")

    p_abl = sub.add_parser("ablation")
    p_abl.add_argument("capsules", nargs="+")
    p_abl.add_argument("--metric")

    p_prov = sub.add_parser("provenance")
    p_prov.add_argument("run")

    p_md = sub.add_parser("metric-dir")
    md_sub = p_md.add_subparsers(dest="md_cmd", required=True)
    p_md_set = md_sub.add_parser("set")
    p_md_set.add_argument("metric")
    p_md_set.add_argument("direction", choices=["higher", "lower"])
    md_sub.add_parser("list")
    md_sub.add_parser("suggest")

    p_graph = sub.add_parser("graph")          # render the graph (read-only)
    g_fmt = p_graph.add_mutually_exclusive_group()
    g_fmt.add_argument("--mermaid", action="store_true",
                       help="emit Mermaid flowchart (default)")
    g_fmt.add_argument("--dot", action="store_true", help="emit Graphviz DOT")
    g_fmt.add_argument("--text", action="store_true",
                       help="emit the plain-text variant-cluster tree")
    p_graph.add_argument("--runs", action="store_true",
                         help="include run nodes + produced/active edges")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "init":
        Store.init(_find_root())
        print(f"initialized .rgit/ in {_find_root()}")
        print("note: run `rgit install-hooks` to capture on every commit")
        return 0

    if args.cmd == "mcp":
        # Serve the graph over MCP. Tools resolve the store lazily (per call,
        # from cwd), so the server itself needs no repo to boot.
        from .mcp_server import run as run_mcp
        run_mcp()
        return 0

    if args.cmd == "install":
        from . import installer
        if args.list or not args.platform:
            print("platforms: " + ", ".join(installer.PLATFORMS))
            return 0
        fn = installer.uninstall if args.uninstall else installer.install
        mode = args.guidance
        if mode is None and not args.uninstall and _stdin_is_tty():
            mode = _prompt_guidance_mode(args.platform)
        res = fn(args.platform, scope=args.scope, dry_run=args.dry_run, mode=mode)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "install-hooks":
        from .hooks import install_hooks, uninstall_hooks
        if args.uninstall:
            res = uninstall_hooks(_find_root())
        else:
            res = install_hooks(_find_root(), dry_run=args.dry_run)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    try:
        store = Store.open()
    except FileNotFoundError:
        if getattr(args, "init", False):
            Store.init(_find_root())
            store = Store.open()
        else:
            msg = "no .rgit/ found; run `rgit init` at the git root"
            if args.cmd in ("run", "capture"):
                msg += " (or pass --init to create it now)"
            print(msg)
            return 1

    if args.cmd == "run":
        cmd = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest
        active = None
        if args.active:
            # accept repeated --with and comma-separated names/ids; resolve to ids
            tokens = [t for chunk in args.active for t in chunk.split(",") if t]
            try:
                active = [store.resolve_feature(t) for t in tokens]
            except KeyError as e:
                print(str(e).strip('"'))
                return 1
        run_id, prop_id = run_experiment(store, cmd, _segmenter(), now=_now(),
                                         from_features=args.from_features,
                                         active=active)
        if args.refresh_guide_file and args.from_features:
            from pathlib import Path
            guide = Path(args.refresh_guide_file).read_text(encoding="utf-8")
            for src in args.from_features:
                store.update_capsule(src, resurrection_guide=guide)
        print(f"run {run_id} recorded; proposal {prop_id} awaiting review")
        if args.from_features:
            print(f"  linked as variant_of: {', '.join(args.from_features)}")
        return 0

    if args.cmd == "capture":
        pid = segment_diff(store, args.trigger, _segmenter(), run_id=None, now=_now())
        print(f"proposal {pid} created")
        return 0

    if args.cmd == "review":
        if args.dismiss:
            dismiss(store, args.dismiss)
            print(f"dismissed {args.dismiss}")
            return 0
        if args.approve:
            fid = approve(store, args.approve, args.index, args.name)
            print(f"approved -> feature {fid}")
            return 0
        for p in store.list_proposals("open"):
            names = ", ".join(c["name"] for c in p.candidates)
            print(f"{p.id}  [{p.trigger}]  candidates: {names}")
        return 0

    if args.cmd == "features":
        for c in store.list_features():
            print(f"{c.id}  {c.name}  — {c.intent}")
        return 0

    if args.cmd == "edges":
        from . import edges as edgesmod
        if args.add:
            etype, src, dst = args.add
            store.add_edge(src, dst, etype)
            print(f"edge {src} -{etype}-> {dst}")
            return 0
        if args.apply:
            pairs = edgesmod.overlap_pairs(store)
            n = edgesmod.apply_overlaps(store)
            cands = edgesmod.depends_candidates(store)
            # overlap_pairs is the agent's worklist: each baseline `overlaps` pair
            # the edge-judge can upgrade to a richer relationship.
            print(json.dumps({"overlaps_written": n,
                              "overlap_pairs": [{"a": a, "b": b} for a, b in pairs],
                              "depends_candidates": cands},
                             indent=2, ensure_ascii=False))
            return 0
        if args.candidates:
            print(json.dumps(edgesmod.depends_candidates(store), indent=2,
                             ensure_ascii=False))
            return 0
        print("nothing to do (use --apply, --candidates, or --add)")
        return 1

    if args.cmd == "pending":
        items = []
        for p in store.list_proposals("open"):
            diff = store.objects.get(p.diff_ref).decode() if p.diff_ref else ""
            items.append({"proposal_id": p.id, "trigger": p.trigger,
                          "diff": diff, "candidates": p.candidates})
        if args.json:
            print(json.dumps(items, indent=2, ensure_ascii=False))
        else:
            for it in items:
                print(f"{it['proposal_id']}  [{it['trigger']}]  "
                      f"{len(it['candidates'])} candidate(s)")
        return 0

    if args.cmd == "resegment":
        import sys
        from pathlib import Path
        raw = sys.stdin.read() if args.from_json == "-" else Path(args.from_json).read_text(encoding="utf-8")
        candidates = json.loads(raw)
        store.set_proposal_candidates(args.proposal_id, candidates)
        print(f"resegmented {args.proposal_id}: {len(candidates)} candidate(s)")
        return 0

    if args.cmd == "watch":
        from . import watch as watchmod
        if args.once:
            snap = watchmod.snapshot(store)
            _, pid = watchmod.tick(store, snap, _now())
            print(f"staged proposal {pid}" if pid else "nothing to capture")
            return 0
        watchmod.loop(store, interval=args.interval, idle=args.idle, now_fn=_now)
        return 0

    if args.cmd == "compare":
        from . import compare as cmpmod
        from .tables import render_table
        try:
            res = cmpmod.compare(store, args.target, args.metric, args.direction)
        except KeyError as e:
            print(str(e).strip('"'))
            return 1
        def _cell(v):
            return str(v) if v is not None else "—"
        rows = [[r["feature"], _cell(r["value"]), _cell(r["delta"])]
                for r in res["rows"]]
        mark = {(i, 1): True for i, r in enumerate(res["rows"]) if r["winner"]}
        print(render_table(["feature", res["metric"] or "metric", "Δ"], rows, mark))
        return 0

    if args.cmd == "ablation":
        from . import ablation as ablmod
        from .tables import render_table
        try:
            grid = ablmod.ablation(store, args.capsules, args.metric)
        except KeyError as e:
            print(str(e).strip('"'))
            return 1
        cols = sorted({m for row in grid["rows"] for m in row["cells"]})
        headers = ["subset"] + cols
        rows, mark = [], {}
        for i, row in enumerate(grid["rows"]):
            label = "+".join(row["subset"]) or "base"
            rows.append([label] + [str(row["cells"].get(m, "—")) if row["cells"].get(m) is not None else "—"
                                   for m in cols])
            for c, m in enumerate(cols, start=1):
                if grid["winners"].get(m) == row["subset"]:
                    mark[(i, c)] = True
        print(render_table(headers, rows, mark))
        return 0

    if args.cmd == "provenance":
        from . import provenance as provmod
        try:
            res = provmod.provenance(store, args.run)
        except KeyError as e:
            print(str(e).strip('"'))
            return 1
        except (FileNotFoundError, TypeError):
            print(f"artifact unavailable for run {args.run}")
            return 1
        for sl in res["slices"]:
            print(f"[{sl['flag']}] {sl['feature']}  {sl['symbol']}")
            if sl["diff"]:
                print(sl["diff"])
        print(f"summary: {res['summary']}")
        return 0

    if args.cmd == "metric-dir":
        from .metricdir import suggest
        if args.md_cmd == "set":
            store.set_metric_direction(args.metric, args.direction)
            print(f"{args.metric} -> {args.direction}")
            return 0
        if args.md_cmd == "list":
            for m, d in store.list_metric_directions().items():
                print(f"{m}: {d}")
            return 0
        if args.md_cmd == "suggest":
            names = sorted({k for r in store.conn.execute("SELECT metrics FROM runs")
                            if r["metrics"] for k in json.loads(r["metrics"])})
            for m, d in suggest(names).items():
                print(f"{m}: {d}  (apply with: rgit metric-dir set {m} {d})")
            return 0

    if args.cmd == "graph":
        import sys
        from . import graphview
        if args.dot:
            render = graphview.to_dot
        elif args.text:
            render = graphview.to_text
        else:
            render = graphview.to_mermaid       # default
        print(render(store, include_runs=args.runs))
        if render is graphview.to_mermaid:
            # stdout stays pure mermaid (pipeable); the tip goes to stderr
            print("tip: paste into https://mermaid.live to view "
                  "(or render with the mermaid CLI)", file=sys.stderr)
        return 0

    return 1


def _find_root():
    import subprocess
    from pathlib import Path
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())

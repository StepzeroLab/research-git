from __future__ import annotations
import argparse
import datetime
import difflib
import json
import os
import re
import sys
from typing import Optional

GUIDANCE_MODES = ("default", "manual-only", "none")
_GUIDANCE_OPTIONS = [
    ("default", "consider capture after meaningful changes (recommended)"),
    ("manual-only", "only when you explicitly ask"),
    ("none", "install skills + MCP only, write no guidance"),
]
_GUIDANCE_SELECTOR_LINES = 7


class _InteractivePromptUnavailable(Exception):
    pass


class _GuidancePromptCancelled(Exception):
    pass


def _prompt_guidance_mode(platform: str) -> str:
    """Interactive picker shown only on a TTY when --guidance was not passed.

    Prompts go to stderr so stdout stays a clean JSON document.
    """
    try:
        return _prompt_guidance_mode_interactive(platform)
    except _InteractivePromptUnavailable:
        return _prompt_guidance_mode_numbered(platform)


def _prompt_platform_numbered(platforms) -> str:
    """Numbered platform picker for a bare `rgit install` when detection
    finds nothing. Prompts go to stderr; accepts an index or a name."""
    sys.stderr.write("\nwhich agent client is this install for?\n\n")
    for i, p in enumerate(platforms, 1):
        sys.stderr.write(f"  {i}) {p}\n")
    sys.stderr.write(f"\nSelect [1-{len(platforms)}]: ")
    choices = {str(i): p for i, p in enumerate(platforms, 1)}
    choices.update({p: p for p in platforms})
    while True:
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError as e:
            raise _GuidancePromptCancelled from e
        if answer in choices:
            return choices[answer]
        sys.stderr.write(f"Please enter 1-{len(platforms)}: ")


def _prompt_guidance_mode_numbered(platform: str) -> str:
    """Fallback picker that accepts 1/2/3 or mode names."""
    sys.stderr.write(
        f"\nresearch-git guidance for {platform} "
        "- how proactive should capture be?\n\n"
        "  1) default      consider capture after meaningful changes (recommended)\n"
        "  2) manual-only  only when you explicitly ask\n"
        "  3) none         install skills + MCP only, write no guidance\n\n"
        "Select [1-3]: "
    )
    choices = {"1": "default", "2": "manual-only", "3": "none",
               "default": "default", "manual-only": "manual-only", "none": "none"}
    while True:
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError as e:
            raise _GuidancePromptCancelled from e
        if answer in choices:
            return choices[answer]
        sys.stderr.write("Please enter 1, 2, or 3: ")


def _prompt_guidance_mode_interactive(platform: str, stderr=None) -> str:
    stderr = stderr or sys.stderr
    if not getattr(stderr, "isatty", lambda: False)():
        raise _InteractivePromptUnavailable
    if os.environ.get("TERM") == "dumb":
        raise _InteractivePromptUnavailable
    if not _selector_ansi_supported(stderr):
        raise _InteractivePromptUnavailable

    index = 0
    first_render = True
    while True:
        if first_render:
            _render_guidance_selector(platform, index, stderr, True)
            first_render = False
        try:
            key = _read_prompt_key()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            raise _InteractivePromptUnavailable from e
        if key == "ctrl-c":
            raise KeyboardInterrupt
        if key == "up":
            index = (index - 1) % len(_GUIDANCE_OPTIONS)
            _render_guidance_selector(platform, index, stderr, False)
        elif key == "down":
            index = (index + 1) % len(_GUIDANCE_OPTIONS)
            _render_guidance_selector(platform, index, stderr, False)
        elif key == "enter":
            return _GUIDANCE_OPTIONS[index][0]
        elif key in ("1", "2", "3"):
            return _GUIDANCE_OPTIONS[int(key) - 1][0]


def _render_guidance_selector(platform: str, index: int, stderr, first_render: bool) -> None:
    if not first_render:
        stderr.write(f"\x1b[{_GUIDANCE_SELECTOR_LINES}F\x1b[J")
    stderr.write(
        f"research-git guidance for {platform} - how proactive should capture be?\n\n"
    )
    for i, (mode, description) in enumerate(_GUIDANCE_OPTIONS):
        pointer = ">" if i == index else " "
        line = f"{pointer} {mode:<11} {description}"
        if i == index:
            line = f"\x1b[7m{line}\x1b[0m"
        stderr.write(f"{line}\n")
    stderr.write("\nUse ↑/↓ to move, Enter to select.\n")
    stderr.flush()


def _selector_ansi_supported(stderr) -> bool:
    if os.name != "nt":
        return True
    return _enable_windows_virtual_terminal(stderr)


def _enable_windows_virtual_terminal(stderr) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        if handle in (0, -1):
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        new_mode = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if new_mode == mode.value:
            return True
        return bool(kernel32.SetConsoleMode(handle, new_mode))
    except Exception:
        return False


def _read_prompt_key() -> str:
    if os.name == "nt":
        return _read_prompt_key_windows()
    return _read_prompt_key_posix()


def _decode_prompt_key(seq: str) -> str:
    if seq in ("\r", "\n"):
        return "enter"
    if seq == "\x03":
        return "ctrl-c"
    if seq in ("1", "2", "3"):
        return seq
    if seq == "\x1b[A":
        return "up"
    if seq == "\x1b[B":
        return "down"
    return "other"


def _read_prompt_key_posix() -> str:
    try:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception as e:
        raise _InteractivePromptUnavailable from e
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode(errors="ignore")
        if ch == "\x1b":
            seq = ch
            while True:
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    break
                seq += os.read(fd, 1).decode(errors="ignore")
                if len(seq) >= 3:
                    break
            return _decode_prompt_key(seq)
        return _decode_prompt_key(ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_prompt_key_windows() -> str:
    try:
        import msvcrt
    except Exception as e:
        raise _InteractivePromptUnavailable from e
    ch = msvcrt.getwch()
    if ch == "\x03":
        return "ctrl-c"
    if ch in ("\r", "\n", "1", "2", "3"):
        return _decode_prompt_key(ch)
    if ch in ("\x00", "\xe0"):
        ch2 = msvcrt.getwch()
        if ch2 == "H":
            return "up"
        if ch2 == "P":
            return "down"
    return "other"

from .curation import approve, decide, dismiss
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


def _brief(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_exit_code(returncode: int) -> int:
    return returncode if returncode > 0 else 1


def _render_install_result(res: dict) -> None:
    """Understand-Anything-style ✓ lines for one platform's install result.

    The full machine-readable document stays available behind --json; this
    view keeps only what a human acts on.
    """
    print(f"{res.get('platform', '?')}:")
    for r in res.get("results", []):
        mark = "✓" if r.get("rc") == 0 else "✗"
        print(f"  {mark} {' '.join(r.get('cmd', []))}")
        if r.get("rc") != 0 and r.get("out"):
            print(f"      {r['out'].splitlines()[0]}")
    for cmd in res.get("planned", []):
        print(f"  • would run: {' '.join(cmd)}")
    if res.get("links"):
        if res.get("ran"):
            print(f"  ✓ skills linked into {res.get('skills_dir')}")
        else:
            print(f"  • would link {len(res['links'])} skill(s) into "
                  f"{res.get('skills_dir')}")
    for e in res.get("errors", []):
        print(f"  ✗ {e.get('link', '')}: {e.get('error')}")
        if e.get("hint"):
            print(f"      hint: {e['hint']}")
    if res.get("would_remove") is not None:
        print(f"  • would remove {len(res['would_remove'])} skill link(s)")
    if res.get("removed") is not None:
        print(f"  ✓ removed {len(res['removed'])} skill link(s)")
    g = res.get("guidance") or {}
    if g:
        action = g.get("action", "?")
        path = g.get("path", "")
        if action == "disabled":
            print("  • guidance: not written (mode none)")
        elif action == "manual":
            print("  • guidance: no managed file for this platform — run with "
                  "--json for the block to paste")
        elif action == "skipped_error":
            print(f"  ✗ guidance: {g.get('error')} ({path})")
        else:
            print(f"  ✓ guidance {action}: {path}".rstrip(": "))
        if g.get("hint"):
            print(f"      hint: {g['hint']}")
    if res.get("instructions"):
        print(f"  → {res['instructions']}")


def _sole_open_proposal(store: Store) -> str:
    """The only open proposal's id; ValueError otherwise.

    Lets `review --approve` / `--dismiss` work without copying an id in the
    overwhelmingly common one-proposal case; ambiguity fails with the listing
    so the retry is a copy-paste.
    """
    opens = store.list_proposals("open")
    if not opens:
        raise ValueError("no pending proposals")
    if len(opens) > 1:
        lines = []
        for p in opens:
            names = ", ".join(c["name"] for c in p.candidates) or "0 candidate(s)"
            lines.append(f"  {p.id}  [{p.trigger}]  {names}")
        raise ValueError("several proposals are open; pass an id:\n"
                         + "\n".join(lines))
    return opens[0].id


def _diff_text(store: Store, diff_ref: Optional[str]) -> str:
    return store.objects.get(diff_ref).decode(errors="replace") if diff_ref else ""


def _skip_notices(diff: str) -> list[str]:
    return [line for line in diff.splitlines()
            if line.startswith("research-git: skipped ")]


def _print_skip_summary(diff: str, indent: str = "") -> None:
    notices = _skip_notices(diff)
    if not notices:
        return
    print(f"{indent}warning: skipped {len(notices)} file(s); "
          "run `rgit pending --json` for details")


def _print_run_result(result, store: Store) -> None:
    prop_id = result.proposal_id
    if prop_id is None:
        print(f"run {result.run_id} recorded; no code changes to capture")
    else:
        prop = store.get_proposal(prop_id)
        print(f"run {result.run_id} recorded; proposal {prop_id} awaiting review")
        _print_skip_summary(_diff_text(store, prop.diff_ref), indent="  ")
        if not prop.candidates:
            print("  note: proposal has 0 candidates; run `rgit pending --json`, "
                  "then `rgit resegment <proposal_id> --from-json <path>`")
    if result.metrics:
        metrics = ", ".join(f"{k}={v}" for k, v in result.metrics.items())
        print(f"  metrics: {metrics}")
    if result.returncode != 0:
        print(f"  command exited with status {result.returncode}")
        err = _brief(result.stderr)
        out = _brief(result.stdout)
        if err:
            print("  stderr:")
            print(err)
        if out:
            print("  stdout:")
            print(out)


class _Parser(argparse.ArgumentParser):
    """ArgumentParser with a git-style did-you-mean on unknown subcommands."""

    commands: tuple = ()

    def error(self, message):
        m = re.search(r"invalid choice: '([^']+)'", message)
        if m and self.commands:
            close = difflib.get_close_matches(m.group(1), self.commands,
                                              n=3, cutoff=0.6)
            if close:
                message += ("\nhint: did you mean "
                            + " or ".join(f"'{c}'" for c in close) + "?")
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="rgit")
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
    p_cap.add_argument("source", nargs="?", metavar="REV|A..B",
                       help="what to capture: a commit (HEAD, abc123) or a "
                            "range (main..HEAD). Omit to auto-pick: the "
                            "working tree if it has changes, else the last "
                            "commit.")
    p_cap.add_argument("--trigger", default="manual")
    # Legacy source spellings — permanent, but hidden: the positional form is
    # the one taught everywhere, and deployed hooks/guidance keep working.
    cap_src = p_cap.add_mutually_exclusive_group()
    cap_src.add_argument("--commit", nargs="?", const="HEAD", default=None,
                         metavar="REF", help=argparse.SUPPRESS)
    cap_src.add_argument("--range", dest="range_spec", default=None,
                         metavar="A..B", help=argparse.SUPPRESS)
    cap_src.add_argument("--worktree", action="store_true",
                         help=argparse.SUPPRESS)
    p_cap.add_argument("--init", action="store_true",
                       help="create .rgit/ at the git root if missing (no hooks)")

    p_rev = sub.add_parser("review")
    p_rev.add_argument("--approve", nargs="?", const="", default=None,
                       metavar="PROPOSAL_ID",
                       help="approve PROPOSAL_ID — or, with no id, the only "
                            "open proposal")
    p_rev.add_argument("--name")
    p_rev.add_argument("--index", type=int, default=0)
    p_rev.add_argument("--dismiss", nargs="?", const="", default=None,
                       metavar="PROPOSAL_ID",
                       help="dismiss PROPOSAL_ID — or, with no id, the only "
                            "open proposal")
    p_rev.add_argument("--decide", nargs="?", const="", default=None,
                       metavar="PROPOSAL_ID",
                       help="decide PROPOSAL_ID in one shot — or, with no id, "
                            "the only open proposal; requires --keep")
    p_rev.add_argument("--keep", default=None, metavar="NAME[,NAME...]",
                       help="with --decide: comma-separated candidate names "
                            "to approve; every other candidate is dropped")

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
    p_inst.add_argument("platform", nargs="?",
                        help="agent client to install for; omit to auto-detect "
                             "every client on this machine")
    p_inst.add_argument("--list", action="store_true",
                        help="list supported platforms")
    p_inst.add_argument("--uninstall", action="store_true")
    # Plumbing — permanent but hidden: --dry-run is the test seam, --scope has
    # the right default, guidance is prompted/auto-defaulted, and --json is the
    # machine-readable document the human lines replaced.
    p_inst.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p_inst.add_argument("--guidance", choices=list(GUIDANCE_MODES),
                        help=argparse.SUPPRESS)
    p_inst.add_argument("--scope", default="user",
                        choices=["user", "project", "local"],
                        help=argparse.SUPPRESS)
    p_inst.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_inst.add_argument("--from-update", dest="from_update",
                        action="store_true", help=argparse.SUPPRESS)

    p_upd = sub.add_parser("update")   # upgrade package + refresh platforms
    upd_grp = p_upd.add_mutually_exclusive_group()
    upd_grp.add_argument("--off", action="store_true",
                         help="permanently disable the update notice")
    upd_grp.add_argument("--on", action="store_true",
                         help="re-enable the update notice")

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

    p_doc = sub.add_parser("doctor")
    p_doc.add_argument("--json", action="store_true")

    parser.commands = tuple(sub.choices)
    return parser


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 so non-ASCII output can't raise UnicodeEncodeError.

    On Windows the console/pipe defaults to the locale codepage (e.g. cp936),
    which can't encode glyphs we emit (•, box-drawing, arrows) or arbitrary
    unicode in capsule names/intents. Kept in its own function so it does not
    depend on `main`'s local `import sys`.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    notice = None
    if args.cmd not in ("mcp", "update") and sys.stdout.isatty() \
            and not getattr(args, "json", False):
        import time
        from . import updatecheck
        from . import __version__
        updatecheck.maybe_start_background_check(time.time())
        notice = updatecheck.render_notice(__version__)

    try:
        return _dispatch(args, parser)
    finally:
        if notice:
            print(notice, file=sys.stderr)


def _dispatch(args, parser) -> int:
    if args.cmd == "update":
        from . import selfupdate, updatecheck
        if args.off or args.on:
            updatecheck.set_disabled(bool(args.off))
            print("update notice " + ("disabled" if args.off else "enabled"))
            return 0
        return selfupdate.run_update()

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
        if args.list:
            print("platforms: " + ", ".join(installer.PLATFORMS))
            return 0
        if args.platform:
            platforms = [args.platform]
        else:
            platforms = installer.detect_platforms()
            if platforms:
                print("detected: " + ", ".join(platforms), file=sys.stderr)
            else:
                if sys.stdin.isatty():
                    try:
                        platforms = [_prompt_platform_numbered(installer.PLATFORMS)]
                    except KeyboardInterrupt:
                        print("\ninstall cancelled", file=sys.stderr)
                        return 130
                    except _GuidancePromptCancelled:
                        print("\ninstall cancelled: no platform selected",
                              file=sys.stderr)
                        return 1
                else:
                    print("no agent client detected; platforms: "
                          + ", ".join(installer.PLATFORMS))
                    print("run `rgit install <platform>`")
                    return 1
        fn = installer.uninstall if args.uninstall else installer.install
        mode = args.guidance
        if getattr(args, "from_update", False):
            pass                       # conservative refresh decides per-file
        elif mode is None and not args.uninstall and not sys.stdin.isatty():
            # Automation must succeed on the first try: keep a previously
            # pinned mode (or write `default`) and say so, instead of exiting
            # with homework to re-run with --guidance.
            print("guidance mode: default — change with --guidance <mode>",
                  file=sys.stderr)
        elif mode is None and not args.uninstall:
            label = ", ".join(platforms)
            try:
                mode = _prompt_guidance_mode(label)
            except KeyboardInterrupt:
                print("\ninstall cancelled", file=sys.stderr)
                return 130
            except _GuidancePromptCancelled:
                print("\ninstall cancelled: no guidance mode selected",
                      file=sys.stderr)
                print("run one of:", file=sys.stderr)
                if len(platforms) == 1:
                    for m in GUIDANCE_MODES:
                        print(f"  rgit install {platforms[0]} --guidance {m}",
                              file=sys.stderr)
                else:
                    for p in platforms:
                        print(f"  rgit install {p} --guidance default",
                              file=sys.stderr)
                return 1
        extra = {}
        if not args.uninstall:
            extra["conservative"] = getattr(args, "from_update", False)
        try:
            results = [fn(p, scope=args.scope, dry_run=args.dry_run, mode=mode,
                          **extra)
                       for p in platforms]
        except ValueError as e:
            print(str(e))
            close = difflib.get_close_matches(platforms[0], installer.PLATFORMS,
                                              n=1, cutoff=0.6)
            if close:
                print(f"hint: did you mean '{close[0]}'?")
            return 1
        if args.json:
            # Explicit platform keeps today's single-object payload; bare
            # installs yield one entry per detected client.
            payload = results[0] if args.platform else results
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        for res in results:
            _render_install_result(res)
        if not args.uninstall:
            print("\nrestart your CLI/agent session to pick up the skills")
            print("note: `rgit install-hooks` enables per-commit capture (opt-in)")
        return 0

    if args.cmd == "install-hooks":
        from .hooks import install_hooks, uninstall_hooks
        if args.uninstall:
            res = uninstall_hooks(_find_root())
        else:
            res = install_hooks(_find_root(), dry_run=args.dry_run)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "doctor":
        from .doctor import error_report, format_report, open_doctor_store, run_doctor
        try:
            store = open_doctor_store()
        except FileNotFoundError as e:
            report = error_report("store_open_failed", str(e), ".rgit")
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                print(format_report(report))
            return 1
        report = run_doctor(store)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(format_report(report))
        return 1 if report["summary"]["errors"] else 0

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
        if not cmd:
            print("no command provided; use `rgit run -- <command>`")
            return 1
        active = None
        if args.active:
            # accept repeated --with and comma-separated names/ids; resolve to ids
            tokens = [t for chunk in args.active for t in chunk.split(",") if t]
            try:
                active = [store.resolve_feature(t) for t in tokens]
            except KeyError as e:
                print(str(e).strip('"'))
                return 1
        result = run_experiment(store, cmd, _segmenter(), now=_now(),
                                from_features=args.from_features,
                                active=active)
        if args.refresh_guide_file and args.from_features:
            from pathlib import Path
            guide = Path(args.refresh_guide_file).read_text(encoding="utf-8")
            for src in args.from_features:
                store.update_capsule(src, resurrection_guide=guide)
        _print_run_result(result, store)
        if args.from_features:
            print(f"  linked as variant_of: {', '.join(args.from_features)}")
        return 0 if result.returncode == 0 else _run_exit_code(result.returncode)

    if args.cmd == "capture":
        from .gitutil import (CommitDiffSource, RangeDiffSource,
                              WorktreeDiffSource, commit_subject, diff_since,
                              resolve_commit)
        explicit_flag = (args.range_spec is not None or args.commit is not None
                         or args.worktree)
        if args.source is not None and explicit_flag:
            print("give either a positional source or "
                  "--commit/--range/--worktree, not both")
            return 1
        try:
            if args.source is not None:
                source = (RangeDiffSource(args.source) if ".." in args.source
                          else CommitDiffSource(args.source))
            elif args.range_spec is not None:
                source = RangeDiffSource(args.range_spec)
            elif args.commit is not None:
                source = CommitDiffSource(args.commit)
            elif args.worktree:
                source = WorktreeDiffSource()
            elif args.trigger == "commit":
                # Deployed post-commit hooks run `rgit capture --trigger commit`
                # with no explicit source. The hook knows its context: right
                # after a partially staged commit the worktree holds leftovers,
                # so auto would capture the wrong thing — the hook must take
                # the commit that just happened.
                source = CommitDiffSource("HEAD")
            elif diff_since(store.root, "HEAD").strip():
                source = WorktreeDiffSource()
            else:
                # Clean tree: the work worth preserving is the last commit.
                # Say which one — a just-pulled foreign commit should be
                # captured visibly and dismissably, never silently.
                sha = resolve_commit(store.root, "HEAD")
                print(f'capturing last commit {sha[:12]} '
                      f'("{commit_subject(store.root, sha)}")')
                source = CommitDiffSource(sha)
            pid = segment_diff(store, args.trigger, _segmenter(), run_id=None,
                               now=_now(), source=source)
        except ValueError as e:
            print(str(e))
            print("hint: pass a commit (HEAD, abc123) or a range (main..HEAD); "
                  "`git log --oneline -5` shows recent commits")
            return 1
        if pid is None:
            print(f"nothing to capture ({source.no_diff_reason(store.root)})")
            return 0
        prop = store.get_proposal(pid)
        created = getattr(pid, "created", True)
        if created:
            print(f"proposal {pid} created")
        else:
            print(f"proposal {pid} already exists for this diff")
        _print_skip_summary(_diff_text(store, prop.diff_ref))
        if created and not prop.candidates:
            print("note: proposal has 0 candidates; run `rgit pending --json`, "
                  "then `rgit resegment <proposal_id> --from-json <path>`")
        return 0

    if args.cmd == "review":
        if args.decide is not None:
            keep = [n.strip() for n in (args.keep or "").split(",") if n.strip()]
            if not keep:
                print("--decide requires --keep NAME[,NAME...]; "
                      "to keep nothing, use --dismiss")
                return 1
            try:
                target = args.decide or _sole_open_proposal(store)
                approved, dropped = decide(store, target, keep)
            except (KeyError, ValueError) as e:
                print(str(e))
                print("hint: inspect with `rgit pending --json`; if there are "
                      "0 candidates, resegment before deciding.")
                return 1
            for name, fid in approved:
                print(f"approved -> {fid}  {name}")
            for name in dropped:
                print(f"dropped     {name}")
            print(f"proposal {target} resolved")
            return 0
        if args.dismiss is not None:
            try:
                target = args.dismiss or _sole_open_proposal(store)
                dismiss(store, target)
            except KeyError as e:
                print(str(e))
                print("hint: run `rgit review` to list open proposals")
                return 1
            except ValueError as e:
                print(str(e))
                return 1
            print(f"dismissed {target}")
            return 0
        if args.approve is not None:
            try:
                target = args.approve or _sole_open_proposal(store)
                fid = approve(store, target, args.index, args.name)
            except (KeyError, ValueError) as e:
                print(str(e))
                print("hint: inspect with `rgit pending --json`; if there are "
                      "0 candidates, resegment before approving.")
                return 1
            print(f"approved -> feature {fid}")
            return 0
        proposals = store.list_proposals("open")
        if not proposals:
            print("no pending proposals")
            return 0
        for p in proposals:
            names = ", ".join(c["name"] for c in p.candidates)
            if names:
                print(f"{p.id}  [{p.trigger}]  candidates: {names}")
            else:
                print(f"{p.id}  [{p.trigger}]  0 candidate(s); "
                      "resegment before approving")
            _print_skip_summary(_diff_text(store, p.diff_ref), indent="  ")
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
            diff = _diff_text(store, p.diff_ref)
            items.append({"proposal_id": p.id, "trigger": p.trigger,
                          "source_commit": p.source_commit,
                          "diff": diff, "candidates": p.candidates})
        if args.json:
            print(json.dumps(items, indent=2, ensure_ascii=False))
        else:
            if not items:
                print("no pending proposals")
                return 0
            for it in items:
                print(f"{it['proposal_id']}  [{it['trigger']}]  "
                      f"{len(it['candidates'])} candidate(s)")
                _print_skip_summary(it["diff"], indent="  ")
        return 0

    if args.cmd == "resegment":
        from pathlib import Path
        if args.from_json == "-":
            # Read stdin as bytes and decode UTF-8: the host agent pipes UTF-8
            # JSON, but sys.stdin.read() would decode with the locale codepage
            # (cp936 on Windows), corrupting non-ASCII intents/names. Fall back to
            # sys.stdin.read() when there is no binary buffer (e.g. patched stdin).
            _buf = getattr(sys.stdin, "buffer", None)
            raw = _buf.read().decode("utf-8") if _buf is not None else sys.stdin.read()
        else:
            raw = Path(args.from_json).read_text(encoding="utf-8")
        from .curation import validate_candidates
        try:
            candidates = json.loads(raw)
            validate_candidates(candidates)
            store.set_proposal_candidates(args.proposal_id, candidates)
        except json.JSONDecodeError as e:
            print(f"invalid JSON: {e}")
            return 1
        except (KeyError, ValueError) as e:
            print(str(e))
            return 1
        print(f"resegmented {args.proposal_id}: {len(candidates)} candidate(s)")
        return 0

    if args.cmd == "watch":
        from . import watch as watchmod
        if args.once:
            snap = watchmod.snapshot(store)
            _, pid = watchmod.tick(store, snap, _now())
            if pid:
                prop = store.get_proposal(pid)
                print(f"staged proposal {pid}")
                _print_skip_summary(_diff_text(store, prop.diff_ref))
                if not prop.candidates:
                    print("note: proposal has 0 candidates; run `rgit pending --json`, "
                          "then `rgit resegment <proposal_id> --from-json <path>`")
            else:
                print("nothing to capture")
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
                         capture_output=True, text=True, check=True,
                         encoding="utf-8", errors="replace")
    return Path(out.stdout.strip())

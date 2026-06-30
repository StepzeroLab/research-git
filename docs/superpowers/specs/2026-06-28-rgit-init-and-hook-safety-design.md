# rgit init / hook-install safety — Design

**Status:** Approved (2026-06-28)

## Problem

`rgit init` does two things of very different character in one breath:

```python
if args.cmd == "init":
    Store.init(_find_root())      # ① create .rgit/ (objects + graph.db) — safe, idempotent
    install_hooks(_find_root())   # ② write .git/hooks/post-commit — side-effectful, clobbers
```

1. **`install_hooks` clobbers.** It unconditionally `write_text`s `.git/hooks/post-commit`, destroying any pre-existing post-commit hook the user had. This is a destructive write into the user's git config with no detection, no backup, no consent.
2. **`init` bundles two concerns.** Creating the data store (safe, idempotent) and wiring a git hook (mutates git config) are fused, so you cannot adopt the store without also taking the hook.
3. **No guidance when the store is missing.** Write commands (`run`, `capture`) call `Store.open()`, which raises an *uncaught* `FileNotFoundError` — the user sees a raw traceback instead of an actionable message.

The driver of these commands is frequently an **agent**, not a human at a TTY. The fix must keep the deterministic engine safe and emit machine-actionable information, leaving judgment (append the line? ask the user?) to the agent layer — consistent with the rest of the architecture (deterministic engine, agentic plane).

## Goals

- `install_hooks` never silently clobbers a foreign hook.
- Hook installation is decoupled from `init` and lives behind its own explicit command.
- Missing-store situations produce clean, actionable messages (no traceback), and write commands can opt into creating the store.

## Non-goals (YAGNI)

- Hook chaining/wrapping (calling the user's old hook from ours).
- Interactive TTY `y/n` prompts.
- Auto-creating `.rgit/` silently from the current working directory.

---

## 1. `install_hooks` becomes safe and agent-friendly

Rewrite `install_hooks` so it classifies the existing `.git/hooks/post-commit` before writing. The existing marker line `# installed by research-git` is the identity tag used to tell *our* hook from a foreign one.

Classification and behavior:

| Existing post-commit | Action |
|---|---|
| absent | write rgit's marked hook |
| ours (contains marker) | overwrite idempotently (safe re-install) |
| foreign (exists, no marker) | **do not touch**; report skip + hand back the line to add |

The function returns a status dict instead of `None`:

```python
def install_hooks(repo: Path) -> dict:
    """Install the post-commit capture hook, never clobbering a foreign hook.

    Returns a status dict:
      {"action": "installed" | "reinstalled" | "skipped_foreign",
       "path": "<abs path to post-commit>",
       "line": "rgit capture --trigger commit"}
    """
```

- `"installed"` — no hook was present; ours was written.
- `"reinstalled"` — our marked hook was already there; rewritten (idempotent).
- `"skipped_foreign"` — a non-rgit hook exists; left untouched. The caller (agent) reads `line` and decides whether to append it or ask the user.

The engine stays dumb-but-safe; all judgment about a foreign hook lives in the agent that reads the status.

`MARKER = "# installed by research-git"` is defined once and shared by install and uninstall so the two never drift.

## 2. New `rgit install-hooks` subcommand

Hook installation leaves `init` and becomes its own command. It mirrors the existing `install` subcommand (which wires the plugin/MCP into an AI client — a distinct concern, no overlap) by printing a JSON status.

```
rgit install-hooks              # install/re-install/skip; prints the status dict as JSON
rgit install-hooks --uninstall  # remove the hook ONLY if it's ours (marker check); refuse foreign
rgit install-hooks --dry-run    # print the action that WOULD be taken; write nothing
```

- Default: call `install_hooks(_find_root())`, print the returned dict as JSON (`json.dumps(..., indent=2, ensure_ascii=False)`), return 0.
- `--uninstall`: if the post-commit hook contains the marker, delete it and report `{"action": "uninstalled", "path": ...}`; if it is foreign, leave it and report `{"action": "skipped_foreign", "path": ...}`; if absent, report `{"action": "absent", "path": ...}`. Return 0 in all cases (nothing went wrong; the status conveys what happened).
- `--dry-run`: classify the existing hook and print the action that *would* be taken (`would_install` / `would_reinstall` / `would_skip_foreign`) without writing.

## 3. `rgit init` = store only

`init` no longer touches `.git/hooks`. It is purely the data-store adoption moment, pinned to the git root.

```python
if args.cmd == "init":
    Store.init(_find_root())
    print(f"initialized .rgit/ in {_find_root()}")
    print("note: run `rgit install-hooks` to capture on every commit")  # discoverability
    return 0
```

The second line preserves discoverability of the hook now that it is opt-in.

## 4. Write commands guide; never silently create

`Store.open()` raising an uncaught `FileNotFoundError` is replaced by centralized handling at the single point where the store is opened (`cli.py`, currently `store = Store.open()`).

- **All other store-backed commands** (`features`, `compare`, `ablation`, `provenance`, `pending`, `edges`, `review`, `resegment`, `watch`, `metric-dir`): catch the error, print the clean message, exit 1 — no traceback. (No `--init`: only `run`/`capture` can self-bootstrap.)
- **`run` / `capture`**: gain a `--init` flag.
  - Without `--init`: same clean actionable error (which additionally mentions `--init`), exit 1.
  - With `--init`: `Store.init(_find_root())` at the git root (store only, **no hooks**), then proceed with the command.

```python
try:
    store = Store.open()
except FileNotFoundError:
    if getattr(args, "init", False):
        Store.init(_find_root()); store = Store.open()
    else:
        msg = "no .rgit/ found; run `rgit init` at the git root"
        if args.cmd in ("run", "capture"):
            msg += " (or pass --init to create it now)"
        print(msg)
        return 1
```

`--init` creates the store at `_find_root()` (the git root), consistent with `rgit init`, **not** at the current working directory — preserving the "is this the right repo?" intentionality. It never installs hooks.

## Error handling summary

| Situation | Behavior |
|---|---|
| `install-hooks`, no existing hook | write ours, report `installed` |
| `install-hooks`, our hook present | overwrite, report `reinstalled` |
| `install-hooks`, foreign hook present | leave it, report `skipped_foreign` + `line` |
| `install-hooks --uninstall`, our hook | delete, report `uninstalled` |
| `install-hooks --uninstall`, foreign hook | leave it, report `skipped_foreign` |
| read-only command, no `.rgit/` | clean message, exit 1, no traceback |
| `run`/`capture`, no `.rgit/`, no `--init` | clean actionable message (mentions `--init`), exit 1 |
| `run`/`capture`, no `.rgit/`, `--init` | create store at git root (no hooks), proceed |

## Testing

- `install_hooks`: fresh install (`installed`); re-install over our marked hook (`reinstalled`, idempotent); foreign hook present → file left byte-identical, returns `skipped_foreign` with `line`.
- `install-hooks --uninstall`: removes our hook; refuses (leaves) a foreign hook; reports `absent` when none.
- `install-hooks --dry-run`: writes nothing; reports the would-be action for each of the three classifications.
- `init`: creates `.rgit/` and does **not** create `.git/hooks/post-commit`.
- `run` / `capture` without store: exit 1 with actionable message; with `--init`: store created at git root and command proceeds.
- read-only command without store: clean exit 1, no traceback.

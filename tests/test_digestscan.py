from conftest import commit_file, merge_branch, revert_head
from rgit.digestscan import scan, unit_id

T0 = 1_700_000_000
DAY = 86_400


def _unit_for(res, sha):
    return next(u for u in res["units"] if sha in u["shas"])


def test_streak_clusters_same_author_related_files(history_repo):
    a = commit_file(history_repo, "m.py", "x = 1\n", "one", when=T0)
    b = commit_file(history_repo, "m.py", "x = 2\n", "two", when=T0 + 3600)
    res = scan(history_repo)
    unit = _unit_for(res, a)
    assert unit["shas"] == [a, b]                 # one unit, oldest -> newest
    assert unit["kind"] == "landed"
    assert unit["meta"]["has_root"] is True
    assert unit["meta"]["subjects"] == ["one", "two"]


def test_streak_breaks_on_author_gap_and_disjoint_files(history_repo):
    a = commit_file(history_repo, "m.py", "x = 1\n", "one", when=T0)
    b = commit_file(history_repo, "m.py", "x = 2\n", "two", when=T0 + 3 * DAY)  # gap
    c = commit_file(history_repo, "other.py", "y = 1\n", "three",
                    when=T0 + 3 * DAY + 60)                                     # no file overlap
    d = commit_file(history_repo, "other.py", "y = 2\n", "four",
                    when=T0 + 3 * DAY + 120, author="someone-else")             # author switch
    res = scan(history_repo)
    units = {tuple(u["shas"]) for u in res["units"]}
    assert (a,) in units and (b,) in units and (c,) in units and (d,) in units


def test_merge_commit_is_its_own_unit(history_repo):
    commit_file(history_repo, "base.py", "b = 1\n", "base", when=T0)
    m = merge_branch(history_repo, [("feat.py", "f = 1\n", "feat work")],
                     "merge feature", when=T0 + 100)
    res = scan(history_repo)
    unit = _unit_for(res, m)
    assert unit["shas"] == [m]
    assert unit["meta"]["merge"] is True
    assert unit["meta"]["files"] == ["feat.py"]


def test_revert_pair_becomes_dead_unit_and_revert_disappears(history_repo):
    commit_file(history_repo, "m.py", "x = 1\n", "base", when=T0)
    exp = commit_file(history_repo, "m.py", "x = 99\n", "wild experiment",
                      when=T0 + 60)
    rev = revert_head(history_repo, when=T0 + 120)
    res = scan(history_repo)
    unit = _unit_for(res, exp)
    assert unit["kind"] == "dead"
    assert unit["shas"] == [exp]
    assert unit["meta"]["dead"] == "reverted"
    assert unit["meta"]["reverted_by"] == rev
    assert unit["meta"]["revert_subject"].startswith("Revert")
    assert all(rev not in u["shas"] for u in res["units"])   # revert consumed


def test_deleted_files_make_dead_unit(history_repo):
    import subprocess
    commit_file(history_repo, "keep.py", "k = 1\n", "keep", when=T0)
    commit_file(history_repo, "gone.py", "g = 1\n", "doomed feature",
                when=T0 + 5 * DAY)
    subprocess.run(["git", "rm", "-q", "gone.py"], cwd=history_repo, check=True,
                   capture_output=True)
    import os
    from conftest import _commit_env
    subprocess.run(["git", "commit", "-q", "-m", "remove it"], cwd=history_repo,
                   check=True, capture_output=True,
                   env={**os.environ, **_commit_env(T0 + 10 * DAY, "t")})
    res = scan(history_repo)
    doomed = next(u for u in res["units"] if u["meta"]["subjects"] == ["doomed feature"])
    assert doomed["kind"] == "dead"
    assert doomed["meta"]["dead"] == "deleted"


def test_infra_only_unit_is_preskipped(history_repo):
    commit_file(history_repo, "m.py", "x = 1\n", "code", when=T0)
    infra = commit_file(history_repo, "README.md", "# docs\n", "docs only",
                        when=T0 + 5 * DAY)
    res = scan(history_repo)
    unit = _unit_for(res, infra)
    assert unit["status"] == "skipped"
    assert unit["skip_reason"] == "infra"


def test_dead_outranks_similar_landed(history_repo):
    a = commit_file(history_repo, "m.py", "x = 1\n", "landed", when=T0)
    b = commit_file(history_repo, "n.py", "y = 1\n", "will die",
                    when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY)
    res = scan(history_repo)
    dead = _unit_for(res, b)
    landed = _unit_for(res, a)
    assert dead["score"] > landed["score"]


def test_window_and_idempotent_ids(history_repo):
    shas = [commit_file(history_repo, "m.py", f"x = {i}\n", f"c{i}",
                        when=T0 + i * 3 * DAY) for i in range(4)]
    res = scan(history_repo, window=2)
    scanned = {s for u in res["units"] for s in u["shas"]}
    assert scanned == set(shas[-2:])              # most recent window only
    assert res["window_applied"] is True
    assert res["total_mainline"] == 4
    res2 = scan(history_repo, window=2)
    assert {u["id"] for u in res["units"]} == {u["id"] for u in res2["units"]}
    assert unit_id(["b", "a"]) == unit_id(["a", "b"])   # order-insensitive


def test_explicit_range_overrides_window(history_repo):
    shas = [commit_file(history_repo, "m.py", f"x = {i}\n", f"c{i}",
                        when=T0 + i * 3 * DAY) for i in range(4)]
    res = scan(history_repo, range_spec=f"{shas[0]}..{shas[2]}", window=1)
    scanned = {s for u in res["units"] for s in u["shas"]}
    assert scanned == {shas[1], shas[2]}
    assert res["window_applied"] is False

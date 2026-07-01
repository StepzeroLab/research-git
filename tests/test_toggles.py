from rgit.toggles import detect_toggles, map_to_capsules
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice

DEACTIVATE_DIFF = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1,3 +1,3 @@
 def loss(x):
-    return entropy(x)
+    # return entropy(x)
     return 0
"""

ACTIVATE_DIFF = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1,3 +1,3 @@
 def loss(x):
-    # return entropy(x)
+    return entropy(x)
     return 0
"""

NON_TOGGLE_DIFF = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1,2 +1,2 @@
 def loss(x):
-    return a
+    return b
"""


def test_detect_deactivate():
    toggles = detect_toggles(DEACTIVATE_DIFF)
    assert len(toggles) == 1
    assert toggles[0]["kind"] == "deactivate"
    assert toggles[0]["file"] == "train.py"


def test_detect_activate():
    toggles = detect_toggles(ACTIVATE_DIFF)
    assert [t["kind"] for t in toggles] == ["activate"]


def test_detect_toggle_handles_timestamp_and_spaces():
    diff = """diff --git a/nested dir/train file.py b/nested dir/train file.py
--- a/nested dir/train file.py
+++ b/nested dir/train file.py\t2026-01-01
@@ -1,3 +1,3 @@
 def loss(x):
-    return entropy(x)
+    # return entropy(x)
     return 0
"""
    toggles = detect_toggles(diff)
    assert toggles == [{"file": "nested dir/train file.py", "line": 2,
                        "kind": "deactivate", "text": "    # return entropy(x)"}]


def test_detect_toggle_handles_c_quoted_path():
    diff = """diff --git \"a/line\\nbreak.py\" \"b/line\\nbreak.py\"
--- \"a/line\\nbreak.py\"
+++ \"b/line\\nbreak.py\"
@@ -1,3 +1,3 @@
 def loss(x):
-    # return entropy(x)
+    return entropy(x)
     return 0
"""
    toggles = detect_toggles(diff)
    assert toggles == [{"file": "line\nbreak.py", "line": 2,
                        "kind": "activate", "text": "    return entropy(x)"}]


def test_detect_toggle_resets_on_dev_null():
    diff = DEACTIVATE_DIFF + """diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null\t2026-01-01
@@ -1,3 +0,0 @@
-def old(x):
-    return entropy(x)
-    return 0
"""
    assert detect_toggles(diff) == [{"file": "train.py", "line": 2,
                                     "kind": "deactivate",
                                     "text": "    # return entropy(x)"}]


def test_non_toggle_edit_is_ignored():
    assert detect_toggles(NON_TOGGLE_DIFF) == []


def test_map_to_capsules_matches_by_file_and_symbol(git_repo):
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text(
        "def loss(x):\n    # return entropy(x)\n    return 0\n")
    fid = store.add_feature(Capsule(
        id="", name="entropy", intent="entropy loss", status="approved",
        base_commit="abc", knobs={}, data_assumptions=None, resurrection_guide="...",
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("train.py", "loss", None, "code", "wrap")]))
    mapped = map_to_capsules(store, detect_toggles(DEACTIVATE_DIFF))
    assert mapped == [{"capsule_id": fid, "kind": "deactivate"}]

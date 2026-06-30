# tests/test_tables.py
from rgit.tables import render_table, render_diff


def test_render_table_aligns_columns_and_marks_winner():
    out = render_table(
        headers=["feature", "eval_loss"],
        rows=[["temperature", "1.18"], ["entropy", "1.10"]],
        mark={(1, 1): True},   # row index 1, col index 1 gets the ★
    )
    lines = out.splitlines()
    assert lines[0].split() == ["feature", "eval_loss"]
    assert "★" in lines[-1]                 # winner row carries the marker
    # every data row is padded to the same visual width
    assert len({len(l) for l in lines}) == 1


def test_render_table_no_marks():
    out = render_table(headers=["a", "b"], rows=[["1", "2"]], mark={})
    assert "★" not in out
    assert "a" in out and "b" in out


def test_render_diff_shows_added_and_removed_lines():
    out = render_diff("def f():\n    return 1\n", "def f():\n    return 2\n",
                      label="model.py:f")
    assert "model.py:f" in out
    assert "-    return 1" in out
    assert "+    return 2" in out


def test_render_diff_identical_is_empty_body():
    out = render_diff("x\n", "x\n", label="same")
    assert out.strip() == "same: (identical)"

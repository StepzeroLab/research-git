from rgit.metrics import parse_metrics


def test_parses_json_file_when_present(tmp_path):
    (tmp_path / "rgit_metrics.json").write_text('{"acc": 0.91, "loss": 0.3}')
    assert parse_metrics("noise", tmp_path) == {"acc": 0.91, "loss": 0.3}


def test_parses_stdout_metric_lines(tmp_path):
    stdout = "epoch 1\nRGIT_METRIC acc=0.88\nRGIT_METRIC loss=0.42\ndone\n"
    assert parse_metrics(stdout, tmp_path) == {"acc": 0.88, "loss": 0.42}


def test_returns_none_when_no_metrics(tmp_path):
    assert parse_metrics("nothing here", tmp_path) is None


def test_skips_malformed_metric_values(tmp_path):
    # one good line, two unparseable ones -> good kept, garbage skipped, no crash
    stdout = "RGIT_METRIC acc=0.9\nRGIT_METRIC bad=1.2.3\nRGIT_METRIC oops=NaNxyz\n"
    assert parse_metrics(stdout, tmp_path) == {"acc": 0.9}


def test_empty_json_file_coalesces_to_none(tmp_path):
    (tmp_path / "rgit_metrics.json").write_text("{}")
    assert parse_metrics("", tmp_path) is None


def test_parses_bare_key_value_and_colon_stdout(tmp_path):
    # the v2 convention: no RGIT_METRIC prefix required; '=' or ':' both work,
    # non-float values (device: cuda) are skipped by the float guard
    stdout = "eval_loss=1.18\neval_accuracy: 0.79\ndevice: cuda\nver=1.2.3\n"
    assert parse_metrics(stdout, tmp_path) == {"eval_loss": 1.18, "eval_accuracy": 0.79}

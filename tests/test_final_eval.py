"""T14: one-shot final test evaluation (OPS-T04, C-02) on the synthetic fixture."""

import json
import math

import jsonschema
import pytest

from conftest import OTHER_KEY
from reviewpulse import cli, tracking
from reviewpulse.final_eval import FinalEvalError, evaluate_test, freeze
from test_evaluation import SCHEMA


@pytest.fixture
def frozen(home_env):
    freeze([home_env["pop_run"], home_env["bpr_run"]])
    return home_env


def _log_lines(home):
    path = home / "reports" / "test_runs.log"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_pop_test_eval_matches_golden(frozen):
    rec = evaluate_test(frozen["pop_run"], confirm_final=True)
    assert rec["formal"] is True and rec["model_kind"] == "Pop"
    assert rec["metrics"]["ndcg_at_10"] == pytest.approx(1 / math.log2(3), abs=1e-9)
    assert rec["metrics"]["recall_at_10"] == pytest.approx(1.0)
    assert rec["counts"]["valid_user_count"] == 1
    assert rec["counts"]["excluded_seen_relevance_count"] == 1
    assert rec["counts"]["excluded_cold_relevance_count"] == 1
    ev = json.loads((frozen["home"] / rec["evaluation_file"]).read_text())
    jsonschema.validate(ev, SCHEMA)
    assert ev["eval_split"] == "test"
    assert _log_lines(frozen["home"]) == [rec]


def test_both_runs_once_share_hashes(frozen):
    a = evaluate_test(frozen["pop_run"], confirm_final=True)
    b = evaluate_test(frozen["bpr_run"], confirm_final=True)
    for key in ("split_sha256", "protocol_sha256", "ground_truth_sha256"):
        assert a[key] == b[key]
    assert [r["run_id"] for r in _log_lines(frozen["home"])] == [
        frozen["pop_run"],
        frozen["bpr_run"],
    ]


# --- OPS-T04: only one formal record per run -------------------------------------------


def test_ops_t04_second_run_refused(frozen):
    evaluate_test(frozen["pop_run"], confirm_final=True)
    with pytest.raises(FinalEvalError, match="already has a formal test record"):
        evaluate_test(frozen["pop_run"], confirm_final=True)
    assert len(_log_lines(frozen["home"])) == 1


# --- refusal paths: nothing is written ---------------------------------------------------


def test_requires_confirm_final(frozen):
    with pytest.raises(FinalEvalError, match="--confirm-final"):
        evaluate_test(frozen["pop_run"])
    assert _log_lines(frozen["home"]) == []


def test_requires_freeze(home_env):
    with pytest.raises(FinalEvalError, match="freeze-test"):
        evaluate_test(home_env["pop_run"], confirm_final=True)


def test_refuses_run_not_frozen(home_env):
    freeze([home_env["pop_run"]])
    with pytest.raises(FinalEvalError, match="not in the frozen list"):
        evaluate_test(home_env["bpr_run"], confirm_final=True)


@pytest.mark.parametrize("config", ["quality.yaml", "train.yaml", "split.yaml"])
def test_refuses_config_drift(frozen, config):
    path = frozen["home"] / "configs" / config
    path.write_text(path.read_text() + "\n# tweaked after looking at test\n")
    with pytest.raises(FinalEvalError, match="changed since freezing"):
        evaluate_test(frozen["pop_run"], confirm_final=True)
    assert _log_lines(frozen["home"]) == []


def test_refuses_dirty_tree(frozen, monkeypatch):
    monkeypatch.setattr(
        tracking, "git_info", lambda cwd=None: {"git_commit": "x", "git_dirty": "true"}
    )
    with pytest.raises(FinalEvalError, match="dirty"):
        evaluate_test(frozen["pop_run"], confirm_final=True)


def test_refuses_other_user_key(frozen, monkeypatch):
    monkeypatch.setenv("RP_USER_KEY", OTHER_KEY)
    with pytest.raises(FinalEvalError, match="user_key_id"):
        evaluate_test(frozen["pop_run"], confirm_final=True)
    assert _log_lines(frozen["home"]) == []


def test_freeze_only_once(frozen):
    with pytest.raises(FinalEvalError, match="already exists"):
        freeze([frozen["pop_run"]])


def test_cli_evaluate_test(frozen, capsys):
    assert cli.main(["evaluate-test", "--run-id", frozen["pop_run"]]) == 1
    assert "--confirm-final" in capsys.readouterr().err
    argv = ["evaluate-test", "--run-id", frozen["pop_run"], "--confirm-final"]
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert cli.main(argv) == 1


def test_written_json_uses_lf_on_every_os(frozen, tmp_path):
    from reviewpulse.io_utils import write_json_atomic

    write_json_atomic({"a": [1, 2]}, tmp_path / "x.json")
    assert b"\r" not in (tmp_path / "x.json").read_bytes()
    rec = evaluate_test(frozen["pop_run"], confirm_final=True)
    for rel in (rec["evaluation_file"], "reports/test_runs.log", "reports/frozen_config.json"):
        assert b"\r" not in (frozen["home"] / rel).read_bytes(), rel


# --- hash corrections for files first written with CRLF on Windows -----------------------


def _simulate_crlf_record(home, rec):
    """Rewrite the log as if the evaluation file had been hashed with CRLF endings."""
    import hashlib

    data = (home / rec["evaluation_file"]).read_bytes()
    crlf_sha = hashlib.sha256(data.replace(b"\n", b"\r\n")).hexdigest()
    log = home / "reports" / "test_runs.log"
    log.write_text(log.read_text().replace(rec["evaluation_sha256"], crlf_sha), newline="\n")
    return crlf_sha


def test_crlf_hash_is_detected_and_corrected_without_editing_records(frozen):
    from reviewpulse.final_eval import correct_evaluation_hash, verify_test_records

    rec = evaluate_test(frozen["pop_run"], confirm_final=True)
    assert verify_test_records() == []
    crlf_sha = _simulate_crlf_record(frozen["home"], rec)
    assert verify_test_records() == [
        f"{rec['run_id']}: evaluation file sha256 differs from the log"
    ]
    original_line = (frozen["home"] / "reports" / "test_runs.log").read_text().splitlines()[0]

    fix = correct_evaluation_hash(rec["run_id"], reason="test")
    assert fix["formal"] is False
    assert fix["original_evaluation_sha256"] == crlf_sha
    assert fix["evaluation_sha256"] == rec["evaluation_sha256"]
    assert verify_test_records() == []
    lines = _log_lines(frozen["home"])
    assert (frozen["home"] / "reports" / "test_runs.log").read_text().splitlines()[
        0
    ] == original_line
    assert sum(1 for r in lines if r.get("formal")) == 1  # OPS-T04 still holds
    assert correct_evaluation_hash(rec["run_id"], reason="again") is None  # idempotent
    with pytest.raises(FinalEvalError, match="already has a formal test record"):
        evaluate_test(frozen["pop_run"], confirm_final=True)


def test_correction_refused_when_content_changed(frozen):
    from reviewpulse.final_eval import correct_evaluation_hash, verify_test_records

    rec = evaluate_test(frozen["pop_run"], confirm_final=True)
    _simulate_crlf_record(frozen["home"], rec)
    f = frozen["home"] / rec["evaluation_file"]
    f.write_text(f.read_text().replace('"ndcg_at_10": 0.6', '"ndcg_at_10": 0.9'), newline="\n")
    with pytest.raises(FinalEvalError, match="beyond line endings"):
        correct_evaluation_hash(rec["run_id"], reason="cover up")
    assert any("metrics differs" in p for p in verify_test_records())
    assert sum(1 for r in _log_lines(frozen["home"]) if r.get("type")) == 0

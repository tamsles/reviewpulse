"""The committed final-evaluation artifacts in reports/ are self-consistent (runs in CI)."""

import json
from pathlib import Path

import pytest

from reviewpulse.final_eval import TEST_LOG, verify_test_records
from reviewpulse.io_utils import sha256_file

REPO = Path(__file__).parents[1]


@pytest.fixture
def repo_home(monkeypatch):
    if not (REPO / TEST_LOG).exists():
        pytest.skip("no final test evaluation recorded in this checkout")
    monkeypatch.setenv("RP_HOME", str(REPO))
    return REPO


def test_committed_test_records_match_their_files(repo_home):
    assert verify_test_records() == []


def test_one_formal_record_per_frozen_run(repo_home):
    frozen = json.loads((repo_home / "reports" / "frozen_config.json").read_text(encoding="utf-8"))
    lines = (repo_home / TEST_LOG).read_text(encoding="utf-8").splitlines()
    formal = [json.loads(x)["run_id"] for x in lines if json.loads(x).get("formal")]
    assert sorted(formal) == sorted(frozen["run_ids"])


def test_configs_unchanged_since_freezing(repo_home):
    frozen = json.loads((repo_home / "reports" / "frozen_config.json").read_text(encoding="utf-8"))
    for name, sha in frozen["config_sha256"].items():
        assert sha256_file(repo_home / "configs" / name) == sha, name


def test_report_files_have_lf_endings(repo_home):
    for f in [repo_home / TEST_LOG, *sorted((repo_home / "reports" / "test_eval").glob("*.json"))]:
        assert b"\r" not in f.read_bytes(), f.name

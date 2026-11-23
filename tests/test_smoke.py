import json
from pathlib import Path

import pytest

import reviewpulse
from reviewpulse import cli, paths

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_reviews.jsonl"


def test_package_imports_and_fixture_present():
    assert reviewpulse.__version__
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 13
    assert all(json.loads(line)["user_id"].startswith("synthetic-") for line in lines)


def test_rp_home_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RP_HOME", str(tmp_path))
    assert paths.resolve("configs/x.yaml") == tmp_path.resolve() / "configs" / "x.yaml"


def test_cli_without_command_fails():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0

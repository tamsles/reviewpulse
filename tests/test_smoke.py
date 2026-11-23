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


def test_git_info_describes_the_code_checkout_not_rp_home(tmp_path, monkeypatch):
    import subprocess

    from reviewpulse import tracking

    monkeypatch.setenv("RP_HOME", str(tmp_path))  # not a git repo
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tracking.CODE_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    assert tracking.git_info()["git_commit"] == head
    assert tracking.git_info(tmp_path) == {"git_commit": "unknown", "git_dirty": "true"}

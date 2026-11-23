"""T13a: end-to-end on the synthetic fixture through the CLI (OPS-T01 evidence) + OPS-T03.

prepare -> split -> train Pop / BPR (2 epochs) -> package -> gate -> promote -> API -> monitor.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import FAKE_KEY, FIXTURES, short
from reviewpulse import cli

REPO = Path(__file__).parents[1]


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def test_e2e_synthetic_pipeline(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    shutil.copytree(REPO / "configs", home / "configs")
    # synthetic inputs instead of the real dataset (C-11: CI never downloads data)
    for name in ("source.yaml", "split.yaml", "train.yaml"):
        shutil.copy(FIXTURES / name, home / "configs" / name)
    shutil.copy(FIXTURES / "synthetic_reviews.jsonl", home / "raw.jsonl")
    src = yaml.safe_load((home / "configs" / "source.yaml").read_text())
    src["source"]["raw_path"] = "raw.jsonl"
    (home / "configs" / "source.yaml").write_text(yaml.safe_dump(src))

    monkeypatch.setenv("RP_HOME", str(home))
    monkeypatch.setenv("RP_USER_KEY", FAKE_KEY)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{(home / 'mlflow.db').as_posix()}")
    monkeypatch.setenv("RP_MLRUNS_DIR", str(home / "mlruns"))
    from reviewpulse import tracking

    # the temporary home is not a git checkout; pretend it is a clean one
    monkeypatch.setattr(
        tracking, "git_info", lambda cwd=None: {"git_commit": "e2e", "git_dirty": "false"}
    )

    assert _run(capsys, "data", "prepare")[0] == 0
    code, split = _run(capsys, "data", "split")
    assert code == 0 and split["windows"]["train"]["interactions"] == 6

    code, pop_runs = _run(capsys, "train", "--model", "Pop")
    assert code == 0
    code, bpr_runs = _run(capsys, "train", "--model", "BPR")
    assert code == 0
    assert pop_runs[0]["metrics"]["ndcg_at_10"] == pytest.approx(1.0)  # golden

    code, pop_rel = _run(capsys, "package", "--run-id", pop_runs[0]["run_id"])
    assert code == 0
    code, bpr_rel = _run(capsys, "package", "--run-id", bpr_runs[0]["run_id"])
    assert code == 0
    pop_id, bpr_id = pop_rel["release_id"], bpr_rel["release_id"]

    code, decision = _run(capsys, "gate", "--release-id", bpr_id)
    assert code == 3 and decision["reason"] == "NOT_BETTER_THAN_POP"
    code, decision = _run(capsys, "gate", "--release-id", pop_id)
    assert code == 0

    code, dry = _run(capsys, "promote", "--release-id", pop_id, "--expected-version", "0")
    assert code == 0 and dry["status"] == "DRY_RUN"
    assert not (home / "state" / "registry.db").exists()
    argv = ["promote", "--release-id", pop_id, "--expected-version", "0", "--execute"]
    code, done = _run(capsys, *argv)
    assert code == 0 and done["status"] == "PROMOTED"
    code, shown = _run(capsys, "champion", "show")
    assert shown["champion"]["release_id"] == pop_id and shown["champion"]["version_counter"] == 1

    from reviewpulse.serving.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/ready").json()["loaded_release_id"] == pop_id
        known = client.post("/v1/recommend", json={"user_id": "synthetic-u1", "top_k": 10})
        unknown = client.post("/v1/recommend", json={"user_id": "someone-new", "top_k": 2})
        bad = client.post("/v1/recommend", json={"user_id": "synthetic-u1", "top_k": 0})
    assert short([i["item_id"] for i in known.json()["items"]]) == ["c", "d"]
    assert unknown.json()["fallback_used"] is False  # Pop scores every user the same way
    assert bad.status_code == 422

    code, summary = _run(capsys, "monitor", "summary", "--min-events", "1")
    assert code == 0
    (group,) = summary["groups"]
    assert group["requests"] == 3 and group["http_4xx"] == 1
    assert summary["online_recommendation_quality"]["status"] == "UNAVAILABLE"


# --- OPS-T03: no data, secrets or state in the repository --------------------------------

FORBIDDEN = [
    r"^data/",
    r"\.parquet$",
    r"\.jsonl\.gz$",
    r"(^|/)\.env$",
    r"(^|/)mlruns/",
    r"(^|/)mlartifacts/",
    r"mlflow\.db$",
    r"^state/",
    r"^releases/",
    r"^logs/",
    r"\.db$",
    r"\.pt$",
]


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.splitlines()


def test_ops_t03_no_data_secrets_or_state_tracked():
    files = _tracked_files()
    assert files, "git ls-files returned nothing"
    bad = [f for f in files if any(re.search(p, f) for p in FORBIDDEN)]
    assert bad == []
    big = [f for f in files if (REPO / f).is_file() and (REPO / f).stat().st_size > 2_000_000]
    assert big == []


def test_ops_t03_no_secret_values_tracked():
    files = [f for f in _tracked_files() if (REPO / f).is_file()]
    env_file = REPO / ".env"
    secrets = []
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                value = line.split("=", 1)[1].strip()
                if len(value) >= 16:
                    secrets.append(value.encode())
    for f in files:
        data = (REPO / f).read_bytes()
        for s in secrets:
            assert s not in data, f"secret value found in tracked file {f}"

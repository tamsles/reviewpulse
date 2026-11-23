"""Thin MLflow wrapper: tracking URI under RP_HOME, git provenance (spec 03, MODEL-04)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow  # noqa: E402

from reviewpulse import paths  # noqa: E402

# src/reviewpulse/tracking.py -> repository root of an editable checkout
CODE_ROOT = Path(__file__).resolve().parents[2]


def configure(experiment: str) -> str:
    """Point MLflow at RP_HOME (unless MLFLOW_TRACKING_URI is set) and select experiment."""
    home = paths.rp_home()
    uri = os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{(home / 'mlflow.db').as_posix()}"
    mlflow.set_tracking_uri(uri)
    if mlflow.get_experiment_by_name(experiment) is None:
        artifacts = Path(os.environ.get("RP_MLRUNS_DIR", home / "mlruns"))
        mlflow.create_experiment(experiment, artifact_location=artifacts.resolve().as_uri())
    mlflow.set_experiment(experiment)
    return uri


def git_info(cwd: Path | None = None) -> dict[str, str]:
    """Commit hash and dirty flag of the code that produced a run.

    Looks at the repository that contains this source code, not at RP_HOME: the data
    home may live elsewhere (e.g. a temp dir in the demo). Outside a git checkout
    (e.g. a pip-installed wheel) the build is reported as unknown and dirty.
    """
    cwd = cwd or CODE_ROOT
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": "true"}
    return {"git_commit": commit, "git_dirty": "true" if status.strip() else "false"}

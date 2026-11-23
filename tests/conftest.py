import json
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_JSONL = FIXTURES / "synthetic_reviews.jsonl"


def read_fixture_rows() -> list[dict]:
    return [json.loads(x) for x in FIXTURE_JSONL.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def source_dict() -> dict:
    return yaml.safe_load((FIXTURES / "source.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def make_source(tmp_path, source_dict):
    """Build a SourceConfig pointing at a tmp copy of the given rows."""
    from reviewpulse.data.contracts import parse_source_config

    def _make(rows: list[dict], name: str = "raw.jsonl", **overrides):
        raw = write_jsonl(tmp_path / name, rows)
        d = json.loads(json.dumps(source_dict))
        d["source"]["raw_path"] = str(raw)
        d["source"].update(overrides)
        return parse_source_config(d)

    return _make


SPLIT_YAML = FIXTURES / "split.yaml"


@pytest.fixture
def golden(tmp_path, make_source):
    """Run the real prepare + split pipeline on the synthetic fixture."""
    import pandas as pd

    from reviewpulse.data.prepare import prepare
    from reviewpulse.data.split import load_split_config, split

    prepare(make_source(read_fixture_rows()), out_dir=tmp_path / "processed")
    cfg = load_split_config(SPLIT_YAML)
    manifest = split(cfg, tmp_path / "processed", tmp_path / "split")
    windows = ("train", "valid", "test")
    frames = {n: pd.read_parquet(tmp_path / "split" / f"{n}.parquet") for n in windows}
    return {"frames": frames, "manifest": manifest, "protocol": cfg.evaluation, "dir": tmp_path}


def short(item_ids):
    """'synthetic-item-a' -> 'a' for readable assertions."""
    return [i.rsplit("-", 1)[-1] for i in item_ids]


FAKE_KEY = "test-only-secret-0123456789abcdef"
OTHER_KEY = "another-test-secret-fedcba9876543210"


@pytest.fixture(scope="session")
def trained_home(tmp_path_factory):
    """Session-wide home with prepare -> split -> train Pop and BPR (2 epochs) done."""
    import shutil

    from reviewpulse import cli
    from reviewpulse.train import train

    home = tmp_path_factory.mktemp("trained")
    (home / "configs").mkdir()
    shutil.copy(FIXTURE_JSONL, home / "raw.jsonl")
    src = yaml.safe_load((FIXTURES / "source.yaml").read_text(encoding="utf-8"))
    src["source"]["raw_path"] = "raw.jsonl"
    (home / "configs" / "source.yaml").write_text(yaml.safe_dump(src))
    for name in ("split.yaml", "train.yaml"):
        shutil.copy(FIXTURES / name, home / "configs" / name)
    for name in ("quality.yaml", "runtime.yaml"):
        shutil.copy(Path(__file__).parents[1] / "configs" / name, home / "configs")
    from reviewpulse import tracking

    mp = pytest.MonkeyPatch()
    for k, v in _home_env(home).items():
        mp.setenv(k, v)
    mp.setattr(tracking, "git_info", _clean_git)
    try:
        assert cli.main(["data", "prepare", "--source", "configs/source.yaml"]) == 0
        assert cli.main(["data", "split", "--split", "configs/split.yaml"]) == 0
        (pop,) = train("Pop", "configs/split.yaml", "configs/train.yaml")
        (bpr,) = train("BPR", "configs/split.yaml", "configs/train.yaml")
    finally:
        mp.undo()
    return {"home": home, "pop_run": pop["run_id"], "bpr_run": bpr["run_id"]}


def _clean_git(cwd=None):
    return {"git_commit": "abc123", "git_dirty": "false"}


def _home_env(home: Path) -> dict[str, str]:
    return {
        "RP_HOME": str(home),
        "RP_USER_KEY": FAKE_KEY,
        "MLFLOW_TRACKING_URI": f"sqlite:///{(home / 'mlflow.db').as_posix()}",
        "RP_MLRUNS_DIR": str(home / "mlruns"),
    }


@pytest.fixture
def home_env(trained_home, tmp_path, monkeypatch):
    """Per-test copy of the trained home, with env vars set and a clean git state."""
    import shutil

    home = tmp_path / "home"
    shutil.copytree(trained_home["home"], home)
    for k, v in _home_env(home).items():
        monkeypatch.setenv(k, v)
    from reviewpulse import tracking

    monkeypatch.setattr(tracking, "git_info", _clean_git)
    return {**trained_home, "home": home}

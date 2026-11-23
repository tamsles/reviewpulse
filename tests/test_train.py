"""T07: `reviewpulse train` + MLflow logging on the synthetic fixture."""

import json
import shutil
from pathlib import Path

import jsonschema
import pytest
import yaml

from conftest import FIXTURES
from reviewpulse import cli

FAKE_KEY = "test-only-secret-0123456789abcdef"
SCHEMA = json.loads(
    (
        Path(__file__).parents[1] / "docs/specs/schemas/evaluation-manifest.v1.2.schema.json"
    ).read_text(encoding="utf-8")
)


def _setup_home(home: Path, source_dict: dict) -> None:
    (home / "configs").mkdir(parents=True)
    shutil.copy(FIXTURES / "synthetic_reviews.jsonl", home / "raw.jsonl")
    source_dict["source"]["raw_path"] = "raw.jsonl"
    (home / "configs" / "source.yaml").write_text(yaml.safe_dump(source_dict))
    shutil.copy(FIXTURES / "split.yaml", home / "configs" / "split.yaml")
    shutil.copy(FIXTURES / "train.yaml", home / "configs" / "train.yaml")


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """prepare -> split -> train Pop -> train BPR once for the whole module."""
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    source = yaml.safe_load((FIXTURES / "source.yaml").read_text(encoding="utf-8"))
    _setup_home(home, source)
    mp.setenv("RP_HOME", str(home))
    mp.setenv("RP_USER_KEY", FAKE_KEY)
    mp.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{(home / 'mlflow.db').as_posix()}")
    mp.setenv("RP_MLRUNS_DIR", str(home / "mlruns"))
    assert cli.main(["data", "prepare", "--source", "configs/source.yaml"]) == 0
    assert cli.main(["data", "split", "--split", "configs/split.yaml"]) == 0

    from reviewpulse.train import train

    pop = train("Pop", "configs/split.yaml", "configs/train.yaml")
    bpr = train("BPR", "configs/split.yaml", "configs/train.yaml")
    yield {"home": home, "pop": pop, "bpr": bpr}
    mp.undo()


def _run(run_id):
    import mlflow

    return mlflow.get_run(run_id)


def _artifact_dir(run) -> Path:
    from urllib.parse import unquote, urlparse

    uri = urlparse(run.info.artifact_uri)
    path = unquote(uri.path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":  # windows: /C:/...
        path = path[1:]
    return Path(path)


def test_pop_run_logged(trained):
    (res,) = trained["pop"]
    run = _run(res["run_id"])
    p = run.data.params
    assert p["model_kind"] == "Pop"
    for key in ("seed", "split_sha256", "protocol_sha256", "normalized_sha256", "git_commit"):
        assert p[key]
    assert p["git_dirty"] in ("true", "false")
    assert run.data.metrics["rec/val/ndcg_at_10"] == pytest.approx(1.0)  # golden
    assert "train_seconds" in run.data.metrics
    files = {f.name for f in _artifact_dir(run).iterdir()}
    assert {"pop.json", "item_mapping.json", "user_mapping.json", "evaluation.json"} <= files


def test_bpr_run_logged(trained):
    (res,) = trained["bpr"]
    run = _run(res["run_id"])
    p = run.data.params
    assert p["model_kind"] == "BPR"
    assert (p["dim"], p["lr"], p["reg"]) == ("64", "0.001", "0.0001")
    assert p["max_epochs"] == "2"
    m = run.data.metrics
    assert 0.0 <= m["rec/val/ndcg_at_10"] <= 1.0
    assert m["epochs_run"] == 2
    files = {f.name for f in _artifact_dir(run).iterdir()}
    assert {
        "model.pt",
        "item_mapping.json",
        "user_mapping.json",
        "pop.json",
        "evaluation.json",
    } <= files


def test_evaluation_artifacts_match_schema_and_hashes(trained):
    (pop,) = trained["pop"]
    (bpr,) = trained["bpr"]
    evs = []
    for res in (pop, bpr):
        d = _artifact_dir(_run(res["run_id"]))
        ev = json.loads((d / "evaluation.json").read_text(encoding="utf-8"))
        jsonschema.validate(ev, SCHEMA)
        assert ev["run_id"] == res["run_id"]
        evs.append(ev)
    # C-03: same split, protocol and ground truth for both models
    for key in ("split_sha256", "protocol_sha256", "ground_truth_sha256"):
        assert evs[0][key] == evs[1][key]


def test_c09_no_raw_user_ids_in_artifacts(trained):
    for res in trained["pop"] + trained["bpr"]:
        run = _run(res["run_id"])
        for f in _artifact_dir(run).iterdir():
            if f.suffix == ".json":
                assert "synthetic-u" not in f.read_text(encoding="utf-8"), f.name
        assert not any("synthetic-u" in str(v) for v in run.data.params.values())
    (bpr,) = trained["bpr"]
    users = json.loads((_artifact_dir(_run(bpr["run_id"])) / "user_mapping.json").read_text())
    assert all(len(k) == 64 for k in users)  # HMAC-SHA256 hex keys


# --- failure paths --------------------------------------------------------------


def test_train_requires_user_key(trained, monkeypatch, capsys):
    monkeypatch.delenv("RP_USER_KEY")
    assert cli.main(["train", "--model", "Pop"]) == 1
    assert "RP_USER_KEY" in capsys.readouterr().err


def test_train_rejects_short_user_key(trained, monkeypatch):
    monkeypatch.setenv("RP_USER_KEY", "short")
    assert cli.main(["train", "--model", "Pop"]) == 1


def test_train_requires_split(tmp_path, monkeypatch, capsys):
    shutil.copytree(FIXTURES, tmp_path / "configs")
    monkeypatch.setenv("RP_HOME", str(tmp_path))
    monkeypatch.setenv("RP_USER_KEY", FAKE_KEY)
    assert cli.main(["train", "--model", "Pop"]) == 1
    assert "split manifest not found" in capsys.readouterr().err


def test_train_rejects_split_config_changed_after_split(trained, monkeypatch, capsys):
    home = trained["home"]
    changed = yaml.safe_load((home / "configs" / "split.yaml").read_text())
    changed["temporal_split"]["train_end"] = "2022-12-01T00:00:00Z"
    (home / "configs" / "split2.yaml").write_text(yaml.safe_dump(changed))
    assert cli.main(["train", "--model", "Pop", "--split", "configs/split2.yaml"]) == 1
    assert "re-run" in capsys.readouterr().err


def test_grid_only_for_bpr_and_capped(trained, monkeypatch, capsys):
    assert cli.main(["train", "--model", "Pop", "--grid"]) == 1
    home = trained["home"]
    cfg = yaml.safe_load((home / "configs" / "train.yaml").read_text())
    cfg["bpr"]["grid"]["dim"] = [8, 16, 32]
    (home / "configs" / "big.yaml").write_text(yaml.safe_dump(cfg))
    argv = ["train", "--model", "BPR", "--grid", "--train-config", "configs/big.yaml"]
    assert cli.main(argv) == 1
    assert "max 8" in capsys.readouterr().err

import copy

import pandas as pd
import pytest
import yaml

from conftest import FIXTURES, read_fixture_rows
from reviewpulse import cli
from reviewpulse.data.contracts import DataContractError
from reviewpulse.data.prepare import prepare
from reviewpulse.data.split import load_split_config, parse_split_config, split

SPLIT_YAML = FIXTURES / "split.yaml"
# 2023-01-01T00:00:00Z / 2023-07-01T00:00:00Z in epoch ms
TRAIN_END_MS = 1672531200000
TEST_END_MS = 1688169600000


@pytest.fixture
def split_dict() -> dict:
    return yaml.safe_load(SPLIT_YAML.read_text(encoding="utf-8"))


def _prepare_and_split(make_source, rows, base, name="raw.jsonl"):
    cfg = make_source(rows, name=name)
    prepare(cfg, out_dir=base / "processed")
    manifest = split(load_split_config(SPLIT_YAML), base / "processed", base / "split")
    return manifest


def _row(user, item, rating, ts):
    return {"user_id": user, "parent_asin": item, "rating": rating, "timestamp": ts, "text": "x"}


# --- happy path -------------------------------------------------------------


def test_split_fixture_counts(make_source, tmp_path):
    m = _prepare_and_split(make_source, read_fixture_rows(), tmp_path)
    w = m["windows"]
    assert (w["train"]["interactions"], w["valid"]["interactions"], w["test"]["interactions"]) == (
        6,
        3,
        4,
    )
    assert w["train"]["users"] == 3 and w["train"]["items"] == 4
    assert (w["valid"]["warm_users"], w["valid"]["cold_users"]) == (3, 0)
    assert (w["test"]["warm_users"], w["test"]["cold_users"]) == (2, 1)  # u4 is cold
    assert m["dropped_at_or_after_test_end"] == 0
    assert m["train_end"] == "2023-01-01T00:00:00Z"
    assert len(m["split_sha256"]) == 64
    train = pd.read_parquet(tmp_path / "split" / "train.parquet")
    assert "source_snapshot_id" not in train.columns
    assert (train["event_time_utc"] < pd.Timestamp("2023-01-01", tz="UTC")).all()


def test_split_boundary_is_half_open(make_source, tmp_path):
    rows = read_fixture_rows() + [_row("synthetic-u5", "synthetic-item-a", 5.0, TRAIN_END_MS)]
    _prepare_and_split(make_source, rows, tmp_path)
    train = pd.read_parquet(tmp_path / "split" / "train.parquet")
    valid = pd.read_parquet(tmp_path / "split" / "valid.parquet")
    assert "synthetic-u5" not in set(train.user_id)
    assert "synthetic-u5" in set(valid.user_id)


def test_split_is_reproducible(make_source, tmp_path):
    m1 = _prepare_and_split(make_source, read_fixture_rows(), tmp_path / "a")
    m2 = _prepare_and_split(make_source, read_fixture_rows()[::-1], tmp_path / "b", name="r.jsonl")
    assert m1["split_sha256"] == m2["split_sha256"]
    for name in ("train", "valid", "test"):
        a = (tmp_path / "a" / "split" / f"{name}.parquet").read_bytes()
        b = (tmp_path / "b" / "split" / f"{name}.parquet").read_bytes()
        assert a == b


def test_cli_split(tmp_path, monkeypatch, source_dict):
    (tmp_path / "raw.jsonl").write_text(
        (FIXTURES / "synthetic_reviews.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    source_dict["source"]["raw_path"] = "raw.jsonl"
    (tmp_path / "source.yaml").write_text(yaml.safe_dump(source_dict))
    (tmp_path / "split.yaml").write_text(SPLIT_YAML.read_text(encoding="utf-8"))
    monkeypatch.setenv("RP_HOME", str(tmp_path))
    assert cli.main(["data", "prepare", "--source", "source.yaml"]) == 0
    assert cli.main(["data", "split", "--split", "split.yaml"]) == 0
    assert (tmp_path / "data" / "split" / "manifest.json").exists()


# --- TE-T01: future injection does not change train window -----------------


@pytest.mark.parametrize(
    "future_ts",
    [
        TEST_END_MS + 86_400_000,  # after test_end: dropped entirely
        TEST_END_MS - 86_400_000,  # inside the test window
    ],
)
def test_te_t01_future_review_does_not_change_train(make_source, tmp_path, future_ts):
    base = read_fixture_rows()
    m1 = _prepare_and_split(make_source, base, tmp_path / "a")
    # the injected review touches an existing train user and item on purpose
    injected = base + [_row("synthetic-u1", "synthetic-item-d", 5.0, future_ts)]
    m2 = _prepare_and_split(make_source, injected, tmp_path / "b", name="inj.jsonl")

    a = (tmp_path / "a" / "split" / "train.parquet").read_bytes()
    b = (tmp_path / "b" / "split" / "train.parquet").read_bytes()
    assert a == b
    assert m1["windows"]["train"] == m2["windows"]["train"]
    # the dataset itself did change, so the lineage hash must differ
    assert m1["normalized_sha256"] != m2["normalized_sha256"]
    if future_ts >= TEST_END_MS:
        assert m2["dropped_at_or_after_test_end"] == 1
        assert m1["split_sha256"] == m2["split_sha256"]
    else:
        assert m2["windows"]["test"]["interactions"] == m1["windows"]["test"]["interactions"] + 1


# --- TE-T03: test-only users are not warm -----------------------------------


def test_te_t03_test_only_user_not_warm(make_source, tmp_path):
    base = read_fixture_rows()
    m1 = _prepare_and_split(make_source, base, tmp_path / "a")
    test_ts = TEST_END_MS - 30 * 86_400_000
    rows = base + [
        _row("synthetic-u9", "synthetic-item-a", 5.0, test_ts),
        _row("synthetic-u9", "synthetic-item-b", 4.0, test_ts + 1000),
    ]
    m2 = _prepare_and_split(make_source, rows, tmp_path / "b", name="u9.jsonl")
    t1, t2 = m1["windows"]["test"], m2["windows"]["test"]
    assert t2["warm_users"] == t1["warm_users"]
    assert t2["cold_users"] == t1["cold_users"] + 1
    assert m2["windows"]["train"]["users"] == m1["windows"]["train"]["users"]


def test_te_t03_valid_activity_does_not_make_user_warm_for_test(make_source, tmp_path):
    # u7 first appears in valid, then in test: still cold for both (warm = before train_end)
    rows = read_fixture_rows() + [
        _row("synthetic-u7", "synthetic-item-a", 5.0, TRAIN_END_MS + 86_400_000),
        _row("synthetic-u7", "synthetic-item-b", 5.0, TEST_END_MS - 86_400_000),
    ]
    m = _prepare_and_split(make_source, rows, tmp_path)
    assert m["windows"]["valid"]["cold_users"] == 1
    assert m["windows"]["test"]["cold_users"] == 2  # u4 and u7


# --- failure paths ----------------------------------------------------------


@pytest.mark.parametrize(
    "patch",
    [
        {"train_end": "2023-04-01T00:00:00Z", "valid_end": "2023-01-01T00:00:00Z"},
        {"valid_end": "2023-07-01T00:00:00Z"},  # valid_end == test_end
        {"train_end": "2023-01-01T00:00:00"},  # no timezone
        {"train_end": "2023-01-01T00:00:00+09:00"},  # not UTC
        {"train_end": None},
        {"timezone": "Asia/Tokyo"},
        {"chronological": False},
    ],
)
def test_split_config_rejects_bad_boundaries(split_dict, patch):
    d = copy.deepcopy(split_dict)
    d["temporal_split"].update(patch)
    with pytest.raises(DataContractError):
        parse_split_config(d)


def test_split_requires_prepared_data(tmp_path):
    with pytest.raises(DataContractError, match="manifest not found"):
        split(load_split_config(SPLIT_YAML), tmp_path / "missing", tmp_path / "split")
    assert not (tmp_path / "split").exists()


def test_split_rejects_tampered_normalized_file(make_source, tmp_path):
    _prepare_and_split(make_source, read_fixture_rows(), tmp_path)
    mpath = tmp_path / "processed" / "manifest.json"
    mpath.write_text(mpath.read_text().replace('"normalized_sha256": "', '"normalized_sha256": "f'))
    with pytest.raises(DataContractError, match="does not match"):
        split(load_split_config(SPLIT_YAML), tmp_path / "processed", tmp_path / "split2")

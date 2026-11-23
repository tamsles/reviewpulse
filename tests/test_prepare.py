import hashlib
import json
import random

import pandas as pd
import pytest

from conftest import FIXTURES, read_fixture_rows
from reviewpulse import cli
from reviewpulse.data.contracts import NORMALIZED_COLUMNS, DataContractError, parse_source_config
from reviewpulse.data.prepare import prepare


def _run(make_source, rows, out_dir, name="raw.jsonl"):
    cfg = make_source(rows, name=name)
    manifest = prepare(cfg, out_dir=out_dir)
    df = pd.read_parquet(out_dir / "interactions.parquet")
    return manifest, df


# --- happy path -------------------------------------------------------------


def test_prepare_fixture_matches_schema(make_source, tmp_path):
    manifest, df = _run(make_source, read_fixture_rows(), tmp_path / "out")
    assert list(df.columns) == list(NORMALIZED_COLUMNS)
    assert len(df) == 13
    assert manifest["record_counts"] == {
        "raw": 13,
        "rejected": 0,
        "duplicates_removed": 0,
        "final": 13,
    }
    assert str(df["event_time_utc"].dt.tz) == "UTC"
    assert (df["source"] == "amazon_review").all()
    assert (df["source_snapshot_id"] == manifest["normalized_sha256"][:16]).all()
    assert df["is_positive"].tolist() == (df["rating"] >= 4.0).tolist()
    # interaction_id = sha256(user|item|event_ms|rating)[:16]
    first = df[(df.user_id == "synthetic-u1") & (df.item_id == "synthetic-item-a")].iloc[0]
    expected = hashlib.sha256(b"synthetic-u1|synthetic-item-a|1667469600000|5.0").hexdigest()
    assert first["interaction_id"] == expected[:16]
    for key in (
        "category",
        "source_url",
        "source_sha256",
        "normalized_sha256",
        "timestamp_unit",
        "positive_rating_threshold",
        "record_counts",
        "license_review_status",
        "created_at_utc",
    ):
        assert key in manifest
    on_disk = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest


def test_prepare_strips_ids_and_keeps_repeat_reviews(make_source, tmp_path):
    rows = read_fixture_rows()
    rows[0]["user_id"] = "  synthetic-u1  "
    _, df = _run(make_source, rows, tmp_path / "out")
    assert "  synthetic-u1  " not in set(df.user_id)
    # u1 reviewed item a twice at different times: both rows are kept
    assert ((df.user_id == "synthetic-u1") & (df.item_id == "synthetic-item-a")).sum() == 2


def test_cli_prepare_uses_rp_home(tmp_path, monkeypatch, source_dict):
    (tmp_path / "configs").mkdir()
    (tmp_path / "raw.jsonl").write_text(
        (FIXTURES / "synthetic_reviews.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    source_dict["source"]["raw_path"] = "raw.jsonl"
    import yaml

    (tmp_path / "configs" / "source.yaml").write_text(yaml.safe_dump(source_dict))
    monkeypatch.setenv("RP_HOME", str(tmp_path))
    assert cli.main(["data", "prepare", "--source", "configs/source.yaml"]) == 0
    assert (tmp_path / "data" / "processed" / "interactions.parquet").exists()


# --- DATA-T01: duplicates ---------------------------------------------------


def test_data_t01_exact_duplicates_removed_deterministically(make_source, tmp_path):
    rows = read_fixture_rows()
    dup = dict(rows[3], text="different text, same key")
    rows = rows + [dup, dict(rows[3])]
    m1, df1 = _run(make_source, rows, tmp_path / "o1", name="a.jsonl")
    m2, df2 = _run(make_source, list(reversed(rows)), tmp_path / "o2", name="b.jsonl")
    assert m1["record_counts"]["raw"] == 15
    assert m1["record_counts"]["duplicates_removed"] == 2
    assert m1["record_counts"]["final"] == 13
    assert df1["interaction_id"].is_unique
    pd.testing.assert_frame_equal(df1, df2)


def test_data_t01_same_key_different_rating_is_not_duplicate(make_source, tmp_path):
    rows = read_fixture_rows()
    rows.append(dict(rows[0], rating=4.0))
    m, _ = _run(make_source, rows, tmp_path / "out")
    assert m["record_counts"]["duplicates_removed"] == 0
    assert m["record_counts"]["final"] == 14


# --- DATA-T02: invalid rating / time rejected ------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("rating", 6.0),
        ("rating", 0.5),
        ("rating", float("nan")),
        ("rating", "5"),
        ("rating", None),
        ("timestamp", "yesterday"),
        ("timestamp", None),
        ("timestamp", 1.5),
        ("timestamp", 10**30),
        ("user_id", "   "),
        ("parent_asin", ""),
    ],
)
def test_data_t02_invalid_record_rejected_and_no_output(make_source, tmp_path, field, value):
    rows = read_fixture_rows()
    rows[5][field] = value
    out = tmp_path / "out"
    with pytest.raises(DataContractError):
        _run(make_source, rows, out)
    assert not out.exists()


def test_data_t02_missing_required_column_rejected(make_source, tmp_path):
    rows = read_fixture_rows()
    del rows[2]["rating"]
    out = tmp_path / "out"
    with pytest.raises(DataContractError, match="missing field"):
        _run(make_source, rows, out)
    assert not out.exists()


def test_data_t02_wrong_unit_detected_by_range_check(make_source, tmp_path):
    # ms data declared as seconds -> far-future dates -> rejected
    cfg = make_source(read_fixture_rows(), timestamp_unit="s")
    with pytest.raises(DataContractError):
        prepare(cfg, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_data_t02_cli_returns_nonzero_on_bad_data(tmp_path, monkeypatch, source_dict):
    import yaml

    rows = read_fixture_rows()
    rows[0]["rating"] = 9
    (tmp_path / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    source_dict["source"]["raw_path"] = "raw.jsonl"
    (tmp_path / "source.yaml").write_text(yaml.safe_dump(source_dict))
    monkeypatch.setenv("RP_HOME", str(tmp_path))
    assert cli.main(["data", "prepare", "--source", "source.yaml"]) == 1
    assert not (tmp_path / "data").exists()


# --- DATA-T03: timestamp unit must be declared ------------------------------


@pytest.mark.parametrize("unit", [None, "missing", "us", "auto"])
def test_data_t03_timestamp_unit_required(source_dict, unit):
    if unit == "missing":
        del source_dict["source"]["timestamp_unit"]
    else:
        source_dict["source"]["timestamp_unit"] = unit
    with pytest.raises(DataContractError, match="timestamp_unit"):
        parse_source_config(source_dict)


# --- DATA-T04: reproducible, row-order independent hash --------------------


def test_data_t04_hash_reproducible_and_order_independent(make_source, tmp_path):
    rows = read_fixture_rows()
    m1, df1 = _run(make_source, rows, tmp_path / "o1", name="a.jsonl")
    m2, _ = _run(make_source, rows, tmp_path / "o2", name="b.jsonl")
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    assert shuffled != rows
    m3, df3 = _run(make_source, shuffled, tmp_path / "o3", name="c.jsonl")

    assert m1["normalized_sha256"] == m2["normalized_sha256"] == m3["normalized_sha256"]
    # raw file bytes differ when shuffled, normalized content does not
    assert m1["source_sha256"] != m3["source_sha256"]
    b1 = (tmp_path / "o1" / "interactions.parquet").read_bytes()
    b3 = (tmp_path / "o3" / "interactions.parquet").read_bytes()
    assert b1 == b3
    pd.testing.assert_frame_equal(df1, df3)


def test_data_t04_hash_changes_when_content_changes(make_source, tmp_path):
    rows = read_fixture_rows()
    m1, _ = _run(make_source, rows, tmp_path / "o1", name="a.jsonl")
    rows[0]["rating"] = 4.0
    m2, _ = _run(make_source, rows, tmp_path / "o2", name="b.jsonl")
    assert m1["normalized_sha256"] != m2["normalized_sha256"]

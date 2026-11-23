"""T05: Pop baseline (REC-T01) on the golden fixture."""

import json
import math

import pandas as pd
import pytest

from conftest import short
from reviewpulse.evaluation.evaluator import build_context, evaluate
from reviewpulse.models.pop import PopModel, PopModelError
from reviewpulse.ranking import ItemSpace, rank

ITEM = "synthetic-item-"


def _ctx(golden, window):
    f = golden["frames"]
    return build_context(f["train"], f[window], golden["protocol"], key_fn=lambda u: u)


def test_rec_t01_pop_scores_and_order(golden):
    pop = PopModel.fit(golden["frames"]["train"])
    assert {short([i])[0]: s for i, s in pop.scores.items()} == {"a": 2, "b": 1, "c": 1, "d": 0}
    assert short(pop.ranked_items()) == ["a", "b", "c", "d"]
    space = ItemSpace.from_items(pop.scores)
    res = rank(pop, "anyone", set(), space, pop, k=10)
    assert short(res.item_ids) == ["a", "b", "c", "d"]
    assert res.scores == [2.0, 1.0, 1.0, 0.0]


def test_rec_t01_pop_golden_metrics_end_to_end(golden):
    pop = PopModel.fit(golden["frames"]["train"])
    sha = golden["manifest"]["split_sha256"]
    val = evaluate(_ctx(golden, "valid"), pop, pop, "r", "validation", sha)
    test = evaluate(_ctx(golden, "test"), pop, pop, "r", "test", sha)
    assert val["metrics"]["ndcg_at_10"] == pytest.approx(1.0, abs=1e-9)
    assert val["metrics"]["recall_at_10"] == pytest.approx(1.0, abs=1e-9)
    assert val["counts"]["valid_user_count"] == 2
    assert val["counts"]["no_relevance_user_count"] == 1
    assert test["metrics"]["ndcg_at_10"] == pytest.approx(1 / math.log2(3), abs=1e-9)
    assert test["metrics"]["recall_at_10"] == pytest.approx(1.0, abs=1e-9)
    assert test["counts"]["valid_user_count"] == 1
    assert test["counts"]["excluded_seen_relevance_count"] == 1
    assert test["counts"]["excluded_cold_relevance_count"] == 1
    assert val["model_kind"] == "Pop"


def test_pop_counts_distinct_positive_users_only(golden):
    train = golden["frames"]["train"]
    # a second positive review of item a by u1 must not raise a's score
    extra = train[train.item_id == ITEM + "a"].head(1).copy()
    extra["interaction_id"] = "dup-user-same-item"
    pop = PopModel.fit(pd.concat([train, extra], ignore_index=True))
    assert pop.scores[ITEM + "a"] == 2


def test_pop_save_load_roundtrip(golden, tmp_path):
    pop = PopModel.fit(golden["frames"]["train"])
    loaded = PopModel.load(pop.save(tmp_path))
    assert loaded.scores == pop.scores
    assert loaded.ranked_items() == pop.ranked_items()


@pytest.mark.parametrize(
    "payload",
    [
        {"format": "other", "kind": "Pop", "scores": {"a": 1}},
        {"format": "reviewpulse.pop.v1", "kind": "Pop", "scores": {}},
        {"format": "reviewpulse.pop.v1", "kind": "Pop", "scores": {"a": -1}},
        {"format": "reviewpulse.pop.v1", "kind": "Pop", "scores": {"a": 1.5}},
        {"format": "reviewpulse.pop.v1", "kind": "Pop", "scores": {"a": True}},
        [],
    ],
)
def test_pop_load_rejects_invalid_file(tmp_path, payload):
    path = tmp_path / "pop.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(PopModelError):
        PopModel.load(path)


def test_pop_load_rejects_corrupt_json(tmp_path):
    path = tmp_path / "pop.json"
    path.write_text("{not json")
    with pytest.raises(PopModelError):
        PopModel.load(path)

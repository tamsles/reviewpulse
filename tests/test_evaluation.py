"""T04: rank() + evaluator. Golden numbers come from docs/design.md (golden case)."""

import copy
import json
import math
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from conftest import short
from reviewpulse.evaluation.evaluator import EvaluationError, build_context, evaluate
from reviewpulse.evaluation.protocol import ProtocolError, protocol_sha256
from reviewpulse.ranking import ItemSpace, RankingError, rank

SCHEMA = json.loads(
    (Path(__file__).parents[1] / "docs/schemas/evaluation-manifest.v1.2.schema.json").read_text(
        encoding="utf-8"
    )
)
ITEM = "synthetic-item-"


class StubModel:
    """Fixed per-item scores; optionally only knows some users."""

    def __init__(self, scores: dict[str, float], kind="Pop", known=None):
        self.kind = kind
        self._scores = scores
        self._known = known

    def knows_user(self, user_key):
        return self._known is None or user_key in self._known

    def score_items(self, user_key, item_ids):
        return np.array([self._scores.get(i, 0.0) for i in item_ids], dtype=float)


# Hand-computed Pop scores for the golden train window: a=2, b=1, c=1, d=0
GOLDEN_POP = StubModel({ITEM + "a": 2, ITEM + "b": 1, ITEM + "c": 1, ITEM + "d": 0})


def _eval(golden, window, model=GOLDEN_POP, fallback=GOLDEN_POP, protocol=None):
    f = golden["frames"]
    split = "validation" if window == "valid" else "test"
    ctx = build_context(f["train"], f[window], protocol or golden["protocol"], key_fn=lambda u: u)
    return ctx, evaluate(
        ctx,
        model,
        fallback,
        run_id="run-test",
        eval_split=split,
        split_sha256=golden["manifest"]["split_sha256"],
    )


# --- TE-T07: golden case ----------------------------------------------------


def test_te_t07_golden_validation(golden):
    ctx, ev = _eval(golden, "valid")
    assert short(ctx.space.items) == ["a", "b", "c", "d"]
    assert ev["metrics"]["ndcg_at_10"] == pytest.approx(1.0, abs=1e-9)
    assert ev["metrics"]["recall_at_10"] == pytest.approx(1.0, abs=1e-9)
    c = ev["counts"]
    assert c["valid_user_count"] == 2
    assert c["no_relevance_user_count"] == 1
    assert c["excluded_seen_relevance_count"] == 0
    assert c["excluded_cold_relevance_count"] == 0
    assert c["fallback_user_count"] == 0
    assert c["eligible_item_count"] == 4
    # u1 -> c, d ; u2 -> b, d ; covered {b, c, d} of 4 eligible
    assert ev["metrics"]["coverage_at_10"] == pytest.approx(3 / 4, abs=1e-9)
    assert ev["eval_split"] == "validation"
    jsonschema.validate(ev, SCHEMA)


def test_te_t07_golden_test(golden):
    _, ev = _eval(golden, "test")
    assert ev["metrics"]["ndcg_at_10"] == pytest.approx(1 / math.log2(3), abs=1e-9)
    assert ev["metrics"]["ndcg_at_10"] == pytest.approx(0.6309, abs=1e-4)
    assert ev["metrics"]["recall_at_10"] == pytest.approx(1.0, abs=1e-9)
    c = ev["counts"]
    assert c["valid_user_count"] == 1
    assert c["excluded_seen_relevance_count"] == 1  # u1 -> a (seen)
    assert c["excluded_cold_relevance_count"] == 1  # u4 -> e (not eligible)
    assert c["no_relevance_user_count"] == 1  # u1 is warm but has no relevance
    jsonschema.validate(ev, SCHEMA)


def test_te_t07_golden_rank_lists(golden):
    ctx, _ = _eval(golden, "valid")
    u1 = rank(GOLDEN_POP, "synthetic-u1", ctx.seen["synthetic-u1"], ctx.space, GOLDEN_POP, k=10)
    u2 = rank(GOLDEN_POP, "synthetic-u2", ctx.seen["synthetic-u2"], ctx.space, GOLDEN_POP, k=10)
    assert short(u1.item_ids) == ["c", "d"]
    assert short(u2.item_ids) == ["b", "d"]
    assert u1.candidate_count == 2


def test_test_seen_stops_at_train_end(golden):
    # EVAL-01.6: valid-window reviews are NOT added to seen for the test split
    ctx, _ = _eval(golden, "test")
    assert short(sorted(ctx.seen["synthetic-u1"])) == ["a", "b"]  # c (valid) not included


# --- TE-T08: low-rated seen items never recommended -------------------------


def test_te_t08_low_rated_seen_items_excluded(golden):
    ctx, _ = _eval(golden, "valid")
    # give the low-rated items the highest scores to make the test meaningful
    model = StubModel({ITEM + "c": 100, ITEM + "d": 100, ITEM + "a": 1, ITEM + "b": 1})
    u2 = rank(model, "synthetic-u2", ctx.seen["synthetic-u2"], ctx.space, GOLDEN_POP, k=10)
    u3 = rank(model, "synthetic-u3", ctx.seen["synthetic-u3"], ctx.space, GOLDEN_POP, k=10)
    assert ITEM + "c" not in u2.item_ids  # u2 rated c with 2 stars
    assert ITEM + "d" not in u3.item_ids  # u3 rated d with 1 star
    assert ITEM + "c" not in u3.item_ids  # u3 rated c with 5 stars


# --- EVAL-T01: |rel| = 0 users ------------------------------------------------


def test_eval_t01_no_relevance_users_counted_not_averaged(golden):
    # u3 has no relevance in valid; if it were averaged as 0 the mean would be 2/3
    _, ev = _eval(golden, "valid")
    assert ev["counts"]["no_relevance_user_count"] == 1
    assert ev["metrics"]["ndcg_at_10"] == pytest.approx(1.0)


# --- EVAL-T02: invalid metrics / no valid users -------------------------------


def test_eval_t02_zero_valid_users_raises(golden):
    f = golden["frames"]
    empty = f["valid"].iloc[0:0]
    ctx = build_context(f["train"], empty, golden["protocol"], key_fn=lambda u: u)
    with pytest.raises(EvaluationError, match="valid users"):
        evaluate(ctx, GOLDEN_POP, GOLDEN_POP, "r", "validation", golden["manifest"]["split_sha256"])


def test_eval_t02_nan_scores_raise(golden):
    nan_model = StubModel({ITEM + "a": float("nan")}, kind="BPR")
    with pytest.raises((EvaluationError, RankingError)):
        _eval(golden, "valid", model=nan_model)


def test_eval_t02_nan_metric_raises(golden, monkeypatch):
    import reviewpulse.evaluation.evaluator as ev_mod

    monkeypatch.setattr(ev_mod, "_ndcg", lambda hits, n_rel, k: float("nan"))
    with pytest.raises(EvaluationError, match="not finite"):
        _eval(golden, "valid")


# --- REC-T04: no seen, no duplicates, contiguous ranks ------------------------


def test_rec_t04_rank_invariants():
    items = [f"i{n:03d}" for n in range(50)]
    space = ItemSpace.from_items(items)
    rng = np.random.default_rng(0)
    scores = {i: float(s) for i, s in zip(items, rng.integers(0, 5, 50), strict=True)}
    model = StubModel(scores)
    seen = {"i001", "i010", "i020", "not-eligible"}
    res = rank(model, "u", seen, space, model, k=10)
    assert len(res.item_ids) == 10
    assert not set(res.item_ids) & seen
    assert len(set(res.item_ids)) == 10
    assert res.ranks == list(range(1, 11))
    assert res.candidate_count == 47
    # sorted by score desc, ties by item_id asc
    pairs = list(zip(res.scores, res.item_ids, strict=True))
    assert pairs == sorted(pairs, key=lambda p: (-p[0], p[1]))


def test_rec_t04_k_larger_than_candidates():
    space = ItemSpace.from_items(["b", "a", "c"])
    assert space.items == ("a", "b", "c")
    m = StubModel({"a": 1, "b": 1, "c": 1})
    res = rank(m, "u", {"b"}, space, m, k=10)
    assert res.item_ids == ["a", "c"]
    assert res.ranks == [1, 2]


@pytest.mark.parametrize("k", [0, -1])
def test_rec_t04_invalid_k(k):
    space = ItemSpace.from_items(["a"])
    m = StubModel({"a": 1})
    with pytest.raises(RankingError):
        rank(m, "u", set(), space, m, k=k)


# --- REC-T05: unknown users fall back to Pop ----------------------------------


def test_rec_t05_unknown_user_falls_back(golden):
    ctx, _ = _eval(golden, "valid")
    bpr_like = StubModel({ITEM + "d": 9}, kind="BPR", known={"synthetic-u1"})
    known = rank(bpr_like, "synthetic-u1", ctx.seen["synthetic-u1"], ctx.space, GOLDEN_POP, k=10)
    unknown = rank(bpr_like, "synthetic-u2", ctx.seen["synthetic-u2"], ctx.space, GOLDEN_POP)
    assert not known.fallback and short(known.item_ids) == ["d", "c"]
    assert unknown.fallback and short(unknown.item_ids) == ["b", "d"]


def test_rec_t05_fallback_user_count(golden):
    bpr_like = StubModel({}, kind="BPR", known={"synthetic-u1"})
    _, ev = _eval(golden, "valid", model=bpr_like)
    assert ev["counts"]["fallback_user_count"] == 1  # u2
    assert ev["model_kind"] == "BPR"


def test_fallback_model_must_know_everyone(golden):
    picky = StubModel({}, known=set())
    with pytest.raises(RankingError, match="fallback"):
        _eval(golden, "valid", model=picky, fallback=picky)


# --- protocol -----------------------------------------------------------------


def test_protocol_hash_ignores_key_order(golden):
    p = golden["protocol"]
    reordered = dict(reversed(list(p.items())))
    assert protocol_sha256(p) == protocol_sha256(reordered)
    changed = dict(p, topk=20)
    assert protocol_sha256(p) != protocol_sha256(changed)


@pytest.mark.parametrize(
    "key,value",
    [
        ("seen_item_policy", "positive_only"),
        ("ranking_mode", "sampled"),
        ("tie_break", "random"),
        ("observed_history_policy", "train_and_valid"),
    ],
)
def test_protocol_rejects_unimplemented_policies(golden, key, value):
    p = copy.deepcopy(golden["protocol"])
    p[key] = value
    with pytest.raises(ProtocolError):
        _eval(golden, "valid", protocol=p)


def test_ground_truth_hash_is_model_independent(golden):
    _, a = _eval(golden, "valid")
    _, b = _eval(golden, "valid", model=StubModel({}, kind="BPR"))
    assert a["ground_truth_sha256"] == b["ground_truth_sha256"]
    assert a["protocol_sha256"] == b["protocol_sha256"]
    _, t = _eval(golden, "test")
    assert t["ground_truth_sha256"] != a["ground_truth_sha256"]

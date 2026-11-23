"""T06: BPR (REC-T02, REC-T03, REC-T06) plus negative-sampling counterexamples."""

import json
import math
import subprocess
import sys

import numpy as np
import pytest

from reviewpulse.evaluation.evaluator import build_context, evaluate
from reviewpulse.models.bpr import BPRConfig, BPRError, BPRModel, sample_negatives
from reviewpulse.models.pop import PopModel
from reviewpulse.ranking import ItemSpace, RankingError, rank

ITEM = "synthetic-item-"
CFG = BPRConfig(dim=8, lr=0.05, reg=1e-4, max_epochs=2, batch_size=2, seed=7)


def _key(u):
    return "key-" + u


def _fit(golden, cfg=CFG, eval_fn=None):
    return BPRModel.fit(golden["frames"]["train"], _key, cfg, eval_fn=eval_fn)


# --- REC-T02: trains on fixture with finite loss ------------------------------


def test_rec_t02_trains_two_epochs_finite_loss(golden):
    model, hist = _fit(golden)
    assert len(hist.losses) == 2
    assert all(math.isfinite(x) for x in hist.losses)
    assert np.isfinite(model.P).all() and np.isfinite(model.Q).all()


def test_bpr_mappings(golden):
    model, _ = _fit(golden)
    # users with >= 1 positive in train: u1 (a, b), u2 (a), u3 (c)
    assert set(model.user_index) == {"key-synthetic-u1", "key-synthetic-u2", "key-synthetic-u3"}
    # all eligible items, including d which only has a 1-star review
    assert set(model.item_index) == {ITEM + x for x in "abcd"}
    assert model.P.shape == (3, 8) and model.Q.shape == (4, 8)


def test_bpr_user_with_only_low_ratings_not_mapped(golden):
    train = golden["frames"]["train"].copy()
    low = train[train.user_id == "synthetic-u3"].copy()
    low["user_id"], low["rating"], low["is_positive"] = "synthetic-u8", 1.0, False
    import pandas as pd

    model, _ = BPRModel.fit(pd.concat([train, low]), _key, CFG)
    assert not model.knows_user("key-synthetic-u8")


def test_bpr_training_is_deterministic(golden):
    a, ha = _fit(golden)
    b, hb = _fit(golden)
    assert ha.losses == hb.losses
    np.testing.assert_array_equal(a.P, b.P)
    np.testing.assert_array_equal(a.Q, b.Q)


def test_bpr_early_stopping_keeps_best_epoch(golden):
    scores = iter([0.5, 0.9, 0.1, 0.1, 0.1, 0.1])
    cfg = BPRConfig(dim=8, lr=0.05, max_epochs=10, patience=3, batch_size=2, seed=7)
    snapshots = []

    def eval_fn(m):
        snapshots.append(m.P.copy())
        return next(scores)

    model, hist = _fit(golden, cfg, eval_fn)
    assert hist.best_epoch == 2
    assert len(hist.val_ndcg) == 5  # stopped after 3 epochs without improvement
    np.testing.assert_array_equal(model.P, snapshots[1])


def test_bpr_evaluates_with_shared_evaluator(golden):
    f = golden["frames"]
    ctx = build_context(f["train"], f["valid"], golden["protocol"], key_fn=_key)
    model, _ = _fit(golden)
    pop = PopModel.fit(f["train"])
    ev = evaluate(ctx, model, pop, "r", "validation", golden["manifest"]["split_sha256"])
    assert ev["model_kind"] == "BPR"
    assert ev["counts"]["fallback_user_count"] == 0
    assert 0.0 <= ev["metrics"]["ndcg_at_10"] <= 1.0


# --- negative sampling counterexamples ---------------------------------------


def test_negatives_never_reviewed_items_including_low_ratings():
    n_items = 5
    # user 0 reviewed items 0..3 (some could be low-rated); only item 4 is a valid negative
    reviewed = np.array(sorted([0 * n_items + i for i in range(4)] + [1 * n_items + 2]))
    users = np.array([0] * 200 + [1] * 200)
    neg = sample_negatives(users, reviewed, n_items, np.random.default_rng(0))
    assert (neg[:200] == 4).all()
    assert not (neg[200:] == 2).any()
    assert set(neg[200:]) == {0, 1, 3, 4}


def test_bpr_negatives_exclude_low_rated_train_items(golden, monkeypatch):
    import reviewpulse.models.bpr as bpr_mod

    captured = []
    real = bpr_mod.sample_negatives

    def spy(users, reviewed, n_items, rng):
        out = real(users, reviewed, n_items, rng)
        captured.append((users.copy(), out.copy()))
        return out

    monkeypatch.setattr(bpr_mod, "sample_negatives", spy)
    model, _ = _fit(golden)
    inv_items = {v: k for k, v in model.item_index.items()}
    inv_users = {v: k for k, v in model.user_index.items()}
    train = golden["frames"]["train"]
    reviewed = {(_key(u), i) for u, i in zip(train.user_id, train.item_id, strict=True)}
    for users, negs in captured:
        for u, j in zip(users, negs, strict=True):
            assert (inv_users[u], inv_items[j]) not in reviewed
    # u2 rated c with 2 stars: c must never be u2's negative
    assert all(
        inv_items[j] != ITEM + "c"
        for users, negs in captured
        for u, j in zip(users, negs, strict=True)
        if inv_users[u] == "key-synthetic-u2"
    )


def test_negative_sampling_impossible_raises(golden):
    import pandas as pd

    train = golden["frames"]["train"]
    full = train.copy()
    rows = [
        dict(full.iloc[0], user_id="synthetic-u9", item_id=ITEM + x, is_positive=True)
        for x in "abcd"
    ]
    with pytest.raises(BPRError, match="every item"):
        BPRModel.fit(pd.concat([train, pd.DataFrame(rows)]), _key, CFG)


# --- REC-T03: save -> new process load -> identical top-10 --------------------

LOAD_SCRIPT = """
import json, sys
from reviewpulse.models.bpr import BPRModel
from reviewpulse.models.pop import PopModel
from reviewpulse.ranking import ItemSpace, rank
m = BPRModel.load(sys.argv[1])
pop = PopModel.load(sys.argv[1] + "/pop.json")
space = ItemSpace.from_items(m.item_index)
out = {}
for user in json.loads(sys.argv[2]):
    r = rank(m, user, [], space, pop, k=10)
    out[user] = [r.item_ids, r.scores, r.fallback]
print(json.dumps(out))
"""


def test_rec_t03_save_load_new_process_same_top10(golden, tmp_path):
    model, _ = _fit(golden)
    pop = PopModel.fit(golden["frames"]["train"])
    model.save(tmp_path)
    pop.save(tmp_path)
    users = ["key-synthetic-u1", "key-synthetic-u2", "key-unknown"]
    space = ItemSpace.from_items(model.item_index)
    expected = {}
    for u in users:
        r = rank(model, u, [], space, pop, k=10)
        expected[u] = [r.item_ids, r.scores, r.fallback]
    proc = subprocess.run(
        [sys.executable, "-c", LOAD_SCRIPT, str(tmp_path), json.dumps(users)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout) == expected
    assert expected["key-unknown"][2] is True


def test_bpr_load_rejects_mapping_shape_mismatch(golden, tmp_path):
    model, _ = _fit(golden)
    model.save(tmp_path)
    items = json.loads((tmp_path / "item_mapping.json").read_text())
    items["synthetic-item-z"] = len(items)
    (tmp_path / "item_mapping.json").write_text(json.dumps(items))
    with pytest.raises(BPRError, match="shape"):
        BPRModel.load(tmp_path)


def test_bpr_load_rejects_missing_file(tmp_path):
    with pytest.raises(BPRError):
        BPRModel.load(tmp_path)


# --- REC-T06: NaN / Inf scores raise ------------------------------------------


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_rec_t06_non_finite_scores_raise(golden, bad):
    model, _ = _fit(golden)
    pop = PopModel.fit(golden["frames"]["train"])
    model.Q[model.item_index[ITEM + "c"], 0] = bad
    space = ItemSpace.from_items(model.item_index)
    with pytest.raises(RankingError, match="non-finite"):
        rank(model, "key-synthetic-u1", [], space, pop, k=10)


def test_rec_t06_non_finite_loss_raises(golden):
    cfg = BPRConfig(dim=8, lr=0.05, max_epochs=2, batch_size=2, seed=7, init_std=float("inf"))
    with pytest.raises(BPRError, match="non-finite"):
        _fit(golden, cfg)


@pytest.mark.parametrize(
    "kwargs", [{"lr": float("nan")}, {"lr": 0.0}, {"reg": -1.0}, {"dim": 0}, {"patience": 0}]
)
def test_bpr_config_rejects_invalid_values(kwargs):
    with pytest.raises(BPRError):
        BPRConfig(**kwargs)

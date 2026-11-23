"""BPR matrix factorization in plain PyTorch (spec 03, MODEL-03; ADR-0009 D2).

score(u, i) = P[u] . Q[i]
loss = -mean(log sigmoid(s(u,i) - s(u,j))) + reg * mean(|P[u]|^2 + |Q[i]|^2 + |Q[j]|^2)
- positives: distinct (u, i) with is_positive in the train window
- negative j: uniform over items u has NOT reviewed in train (any rating).
  "Not reviewed" means unobserved, not disliked (C-12).
- user mapping: users with >= 1 positive in train; item mapping: all eligible items.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

MODEL_FILE = "model.pt"
USER_MAPPING_FILE = "user_mapping.json"
ITEM_MAPPING_FILE = "item_mapping.json"
BPR_FORMAT = "reviewpulse.bpr.v1"
MAX_NEGATIVE_ROUNDS = 100


class BPRError(ValueError):
    """Raised for invalid BPR training state or model files."""


@dataclass(frozen=True)
class BPRConfig:
    dim: int = 64
    lr: float = 1e-3
    reg: float = 1e-4
    max_epochs: int = 50
    patience: int = 5
    batch_size: int = 4096
    seed: int = 42
    init_std: float = 0.1

    def __post_init__(self) -> None:
        for name in ("dim", "max_epochs", "patience", "batch_size"):
            if int(getattr(self, name)) < 1:
                raise BPRError(f"BPRConfig.{name} must be >= 1")
        if not (math.isfinite(self.lr) and self.lr > 0):
            raise BPRError("BPRConfig.lr must be a positive finite number")
        if not (math.isfinite(self.reg) and self.reg >= 0):
            raise BPRError("BPRConfig.reg must be a non-negative finite number")


@dataclass
class TrainHistory:
    losses: list[float]
    val_ndcg: list[float]
    best_epoch: int
    seconds: float


def _weights(emb: torch.nn.Embedding) -> np.ndarray:
    return emb.weight.detach().numpy().copy()


def _write_json(obj: object, path: Path) -> None:
    path.write_text(json.dumps(obj, sort_keys=True) + "\n", encoding="utf-8")


class BPRModel:
    kind = "BPR"

    def __init__(self, user_index: dict[str, int], item_index: dict[str, int], P, Q, cfg):
        self.user_index = user_index
        self.item_index = item_index
        self.cfg = cfg
        self._set_weights(P, Q)

    def _set_weights(self, P: np.ndarray, Q: np.ndarray) -> None:
        if P.shape != (len(self.user_index), self.cfg.dim):
            raise BPRError(f"user embedding shape {P.shape} != mapping {len(self.user_index)}")
        if Q.shape != (len(self.item_index), self.cfg.dim):
            raise BPRError(f"item embedding shape {Q.shape} != mapping {len(self.item_index)}")
        self.P = np.ascontiguousarray(P, dtype=np.float32)
        self.Q = np.ascontiguousarray(Q, dtype=np.float32)
        self._cache_key: tuple[str, ...] | None = None
        self._cache_idx: np.ndarray | None = None

    # --- Recommender interface ------------------------------------------------

    def knows_user(self, user_key: str) -> bool:
        return user_key in self.user_index

    def _item_rows(self, item_ids: Sequence[str]) -> np.ndarray:
        if item_ids is self._cache_key and self._cache_idx is not None:
            return self._cache_idx
        try:
            idx = np.fromiter((self.item_index[i] for i in item_ids), dtype=np.int64)
        except KeyError as exc:
            raise BPRError(f"item {exc.args[0]!r} is not in the BPR item mapping") from exc
        if isinstance(item_ids, tuple):
            self._cache_key, self._cache_idx = item_ids, idx
        return idx

    def score_items(self, user_key: str, item_ids: Sequence[str]) -> np.ndarray:
        u = self.user_index[user_key]
        return self.Q[self._item_rows(item_ids)] @ self.P[u]

    # --- training ---------------------------------------------------------------

    @classmethod
    def fit(
        cls,
        train: pd.DataFrame,
        key_fn: Callable[[str], str],
        cfg: BPRConfig,
        eval_fn: Callable[[BPRModel], float] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> tuple[BPRModel, TrainHistory]:
        """Train with early stopping on `eval_fn` (validation NDCG@10) if given."""
        start = time.perf_counter()
        items = sorted(set(train["item_id"]))
        item_index = {i: n for n, i in enumerate(items)}
        pos = train.loc[train["is_positive"], ["user_id", "item_id"]].drop_duplicates()
        if pos.empty:
            raise BPRError("no positive interactions in train")
        key_of = {u: key_fn(u) for u in pos["user_id"].unique()}
        user_index = {k: n for n, k in enumerate(sorted(key_of.values()))}
        if len(user_index) != len(key_of):
            raise BPRError("user_key collision")

        # user index for every train row of a mapped user (for negative exclusion)
        n_items = len(items)
        mapped = train[train["user_id"].isin(key_of.keys())]
        rev_u = mapped["user_id"].map(key_of).map(user_index).to_numpy(np.int64)
        rev_i = mapped["item_id"].map(item_index).to_numpy(np.int64)
        reviewed = np.unique(rev_u * n_items + rev_i)
        if (np.bincount(reviewed // n_items, minlength=len(user_index)) >= n_items).any():
            raise BPRError("a user reviewed every item; no negatives can be sampled")

        pu = pos["user_id"].map(key_of).map(user_index).to_numpy(np.int64)
        pi = pos["item_id"].map(item_index).to_numpy(np.int64)
        order = np.lexsort((pi, pu))  # deterministic sample order before shuffling
        pu, pi = pu[order], pi[order]

        rng = np.random.default_rng(cfg.seed)
        gen = torch.Generator().manual_seed(cfg.seed)
        P = torch.nn.Embedding(len(user_index), cfg.dim, sparse=True)
        Q = torch.nn.Embedding(n_items, cfg.dim, sparse=True)
        with torch.no_grad():
            P.weight.normal_(0.0, cfg.init_std, generator=gen)
            Q.weight.normal_(0.0, cfg.init_std, generator=gen)
        opt = torch.optim.SparseAdam(list(P.parameters()) + list(Q.parameters()), lr=cfg.lr)

        model = cls(user_index, item_index, _weights(P), _weights(Q), cfg)
        history = TrainHistory(losses=[], val_ndcg=[], best_epoch=0, seconds=0.0)
        best = -math.inf
        best_weights = (model.P.copy(), model.Q.copy())
        bad_epochs = 0

        for epoch in range(1, cfg.max_epochs + 1):
            perm = rng.permutation(len(pu))
            neg = sample_negatives(pu[perm], reviewed, n_items, rng)
            total, count = 0.0, 0
            for s in range(0, len(perm), cfg.batch_size):
                b = perm[s : s + cfg.batch_size]
                u = torch.from_numpy(pu[b])
                i = torch.from_numpy(pi[b])
                j = torch.from_numpy(neg[s : s + cfg.batch_size])
                pu_e, qi_e, qj_e = P(u), Q(i), Q(j)
                # BPR pairwise loss: the positive item should outscore the negative
                x_uij = (pu_e * qi_e).sum(1) - (pu_e * qj_e).sum(1)
                bpr = -F.logsigmoid(x_uij).mean()
                l2 = (pu_e.pow(2).sum(1) + qi_e.pow(2).sum(1) + qj_e.pow(2).sum(1)).mean()
                loss = bpr + cfg.reg * l2
                opt.zero_grad()
                loss.backward()
                opt.step()
                total += float(loss.detach()) * len(b)
                count += len(b)
            epoch_loss = total / count
            if not math.isfinite(epoch_loss):
                raise BPRError(f"non-finite training loss at epoch {epoch}")
            history.losses.append(epoch_loss)
            model._set_weights(_weights(P), _weights(Q))

            if eval_fn is None:
                history.best_epoch = epoch
                best_weights = (model.P, model.Q)
                continue
            score = eval_fn(model)
            history.val_ndcg.append(score)
            if log:
                log(f"epoch {epoch} loss={epoch_loss:.5f} val_ndcg@10={score:.5f}")
            if score > best:
                best, bad_epochs, history.best_epoch = score, 0, epoch
                best_weights = (model.P, model.Q)
            else:
                bad_epochs += 1
                if bad_epochs >= cfg.patience:
                    break

        model._set_weights(*best_weights)
        history.seconds = time.perf_counter() - start
        return model, history

    # --- persistence --------------------------------------------------------------

    def save(self, directory: str | Path) -> list[Path]:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "format": BPR_FORMAT,
                "config": asdict(self.cfg),
                "P": torch.from_numpy(self.P),
                "Q": torch.from_numpy(self.Q),
            },
            d / MODEL_FILE,
        )
        _write_json(self.user_index, d / USER_MAPPING_FILE)
        _write_json(self.item_index, d / ITEM_MAPPING_FILE)
        return [d / MODEL_FILE, d / USER_MAPPING_FILE, d / ITEM_MAPPING_FILE]

    @classmethod
    def load(cls, directory: str | Path) -> BPRModel:
        d = Path(directory)
        try:
            state = torch.load(d / MODEL_FILE, map_location="cpu", weights_only=True)
            users = json.loads((d / USER_MAPPING_FILE).read_text(encoding="utf-8"))
            items = json.loads((d / ITEM_MAPPING_FILE).read_text(encoding="utf-8"))
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            raise BPRError(f"cannot load BPR model: {exc}") from exc
        if not isinstance(state, dict) or state.get("format") != BPR_FORMAT:
            raise BPRError("not a reviewpulse BPR model file")
        for name, mapping in (("user", users), ("item", items)):
            if sorted(mapping.values()) != list(range(len(mapping))):
                raise BPRError(f"{name} mapping indices are not 0..n-1")
        cfg = BPRConfig(**state["config"])
        return cls(users, items, state["P"].numpy(), state["Q"].numpy(), cfg)


def sample_negatives(
    users: np.ndarray, reviewed: np.ndarray, n_items: int, rng: np.random.Generator
) -> np.ndarray:
    """Uniform negatives, resampling any item the user reviewed in train.

    `reviewed` is the sorted array of user * n_items + item codes.
    """
    neg = rng.integers(0, n_items, size=len(users))
    todo = np.arange(len(users))
    for _ in range(MAX_NEGATIVE_ROUNDS):
        codes = users[todo] * n_items + neg[todo]
        pos = np.searchsorted(reviewed, codes)
        hit = (pos < len(reviewed)) & (reviewed[np.minimum(pos, len(reviewed) - 1)] == codes)
        todo = todo[hit]
        if todo.size == 0:
            return neg
        neg[todo] = rng.integers(0, n_items, size=todo.size)
    raise BPRError("negative sampling did not converge")

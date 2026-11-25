# ReviewPulse Design

This document describes what the system guarantees and how each guarantee is enforced. The real-data numbers are in [`reports/experiment.md`](../reports/experiment.md). Machine-readable contracts are in [`schemas/`](schemas/) and synthetic examples in [`examples/`](examples/).

## 1. Invariants

Every rule below is backed by at least one test.

| ID | Rule | Enforced by |
|---|---|---|
| C-01 | Training data, features and seen-item sets use only data before `train_end`. | Future-injection test: adding a later review leaves the train file byte-identical. |
| C-02 | Mappings, Pop scores and thresholds come from the train window only. Tuning uses validation; test is evaluated once, after the config is frozen. | `reports/frozen_config.json`, `reports/test_runs.log`, integrity tests in CI |
| C-03 | Compared models must share the same split, protocol and ground truth (three hashes). | Gate rejects with `PROTOCOL_MISMATCH` / `SPLIT_MISMATCH` |
| C-04 | Model, mappings, seen set, eligible set and Pop fallback ship in one release bundle and are hash-checked on load. | Tampering with a mapping makes the load fail |
| C-05 | The gate reads only the registered evaluation file and has no score argument. | `gate(release_id)` signature |
| C-06 | The champion pointer is updated with a compare-and-swap (CAS) on `version_counter`. A failed CAS changes nothing. | Concurrency and stale-version tests |
| C-07 | The service reports the release it actually loaded, not the latest pointer on disk. | `/ready` test after a promotion without restart |
| C-08 | With no champion the service still starts; `/ready` returns 503; nothing loads at import time. | Startup tests |
| C-09 | Logs, events and release bundles contain no raw `user_id` and no review text. | Scanning tests over all artifacts |
| C-10 | Promotion and rollback are dry-run by default; only `--execute` writes. | Dry-run test |
| C-11 | CI is offline and deterministic and uses only a synthetic fixture. | CI configuration |
| C-12 | Honest wording: a rated review is not a purchase, a missing review is not a negative, offline NDCG is not online CTR, and a losing model is reported as losing. | Report review |

## 2. Data and time split

- **Source**: one category of Amazon Reviews'23 (`All_Beauty`). Only the download script, configs and manifests are committed.
- **Contract**: `user_id` non-empty, `parent_asin` used as `item_id`, `rating` finite in [1, 5], timestamp unit declared explicitly in `configs/source.yaml` (a missing unit is an error). Any invalid row fails the whole batch.
- **Normalization**: exact duplicates `(user_id, item_id, event_time, rating)` are dropped and counted. `is_positive = rating >= 4.0`. This is a positive review, not a click or purchase label.
- **Determinism**: `normalized_sha256` is a canonical text hash over sorted rows, so it does not depend on input row order or the parquet library version.
- **Global time split**, all windows half-open: train `< train_end`, valid `[train_end, valid_end)`, test `[valid_end, test_end)`. No k-core or other filtering. `split_sha256` hashes the sorted interaction IDs of all three windows.

## 3. Offline evaluation protocol

Let `C = train_end` and `W` be the evaluation window.

1. **seen(u)**: every item `u` reviewed before `C`, with any rating, low ratings included. These items are never recommended.
2. **eligible**: items with at least one review before `C`.
3. **Ground truth** `rel(u, W)` = items `u` reviewed positively in `W`, minus `seen(u)`, intersected with `eligible`. Items removed because they were seen go to `excluded_seen_relevance_count`. Items removed because they are cold go to `excluded_cold_relevance_count`.
4. **Valid user**: has any review before `C` and `|rel| >= 1`. Users with `|rel| = 0` are excluded from the average and counted in `no_relevance_user_count`.
5. Valid users the model does not know are scored by Pop and counted in `fallback_user_count`.
6. For the test window, `seen(u)` still stops at `C`. Validation reviews are not added to it.

**Metrics**: NDCG@10 (binary relevance) and Recall@10 (denominator `|rel|`), both averaged per user. Coverage@10 = distinct items in all top-10 lists / `|eligible|`. Ranking is **full ranking** over `eligible - seen(u)`, with no sampled negatives. Ties are broken by `item_id` ascending. The evaluator raises an error on NaN metrics or zero valid users instead of returning 0.

**One ranking function**: offline evaluation and the API call the same `rank()` (`src/reviewpulse/ranking.py`), so filtering, tie-breaking and fallback cannot drift apart.

### Golden case (hand-computed, tested exactly)

Data: [`examples/synthetic_reviews.jsonl`](examples/synthetic_reviews.jsonl). Split: `train_end=2023-01-01`, `valid_end=2023-04-01`, `test_end=2023-07-01`.

- Train: u1: a(5), b(4); u2: a(4), c(2); u3: c(5), d(1). seen: u1={a,b}, u2={a,c}, u3={c,d}; eligible={a,b,c,d}.
- Pop scores (distinct positive reviewers before `C`): a=2, b=1, c=1, d=0, so the order is a, b, c, d (b and c tie, broken by id).

| Window | Outcome for Pop |
|---|---|
| validation | u1 rel={c}, candidates c,d, hit at rank 1. u2 rel={b}, candidates b,d, hit at rank 1. u3 rated a with 3, so rel is empty and u3 is excluded. NDCG@10 = 1.0, Recall@10 = 1.0, 2 valid users, 1 no-relevance user. |
| test | u1 rates a with 5: already seen, `excluded_seen_relevance_count = 1`. u4 rates e with 4: e is cold, `excluded_cold_relevance_count = 1`. u2 rates d with 5: rel={d}, candidates b,d, hit at rank 2. NDCG@10 = 1/log2(3) ~ 0.6309, Recall@10 = 1.0, 1 valid user. |

Items a user rated low during training never appear as candidates (u2's c, u3's d).

## 4. Models

Both models implement `score_items(user_key, item_ids)` and `knows_user(user_key)`. Filtering, top-K and fallback are done by `rank()`.

- **Pop**: score = number of distinct users who reviewed the item positively before `C`. It is deterministic, has no parameters, and is both the cold-start fallback and the gate baseline.
- **BPR** (plain PyTorch, about 100 lines): `s(u,i) = P[u] . Q[i]`. Positives are the train-window positive pairs. For each positive, one negative is sampled uniformly from items the user never reviewed in train. Loss: `-mean(log sigmoid(s(u,i) - s(u,j))) + reg * (|P[u]|^2 + |Q[i]|^2 + |Q[j]|^2)`. The grid is `dim {32,64}` x `lr {1e-3,5e-3}` x `reg {1e-5,1e-4}`, with early stopping on validation NDCG@10 (patience 5, up to 50 epochs) and a fixed seed. A negative only means "not observed", not "disliked".
- Every training run (Pop included) is an MLflow run with the hashes, git commit and dirty flag as params, the validation metrics, and the model, mappings and `evaluation.json` as artifacts.

## 5. Release bundle

```text
releases/<release_id>/
  manifest.json        # schemas/release-manifest.v1.2.schema.json
  model.pt | pop.json
  user_mapping.json    # keyed by user_key
  item_mapping.json
  seen_items.json      # keyed by user_key, seen items up to C
  eligible_items.json
  pop_fallback.json    # [item_id, score] in Pop order; same item set as eligible
  evaluation.json      # validation evaluation manifest
```

- `user_key = HMAC-SHA256(RP_USER_KEY, user_id)`. The key comes from the environment only. The manifest stores a 16-hex-character key fingerprint, so a wrong key is detected at startup.
- Every file has a sha256 in the manifest. `release_id = "rel-" + sha256(canonical manifest without id and timestamp)[:12]`.
- The loader is shared by the service and the gate. It fails with `BundleInvalid(reason)` on a missing file, hash mismatch, mapping/model shape mismatch, missing or wrong key, or a failed smoke inference (one known user, one unknown user).
- A dirty-worktree build can be packaged for development, but the gate always rejects it.

## 6. Quality gate

The only input is `release_id`. Checks run in order and the first failure rejects:

| # | Check | Reason on failure |
|---|---|---|
| 1 | Bundle loads completely | `BUNDLE_INVALID` |
| 2 | `git_dirty == false` | `DIRTY_BUILD` |
| 3 | Evaluated on validation, metrics finite | `INVALID_METRICS` |
| 4 | `ndcg_at_10 >= min_abs_ndcg` | `BELOW_MIN` |
| 5 | Non-Pop only: a loadable Pop release with the same three hashes exists, and `ndcg > pop_ndcg + min_improvement` | `PROTOCOL_MISMATCH` / `NOT_BETTER_THAN_POP` |
| 6 | If a champion exists: same split and protocol hashes, and `ndcg >= champion_ndcg - max_regression` | `SPLIT_MISMATCH` / `REGRESSION` |

Pop is exempt from check 5 because it is the baseline. So when BPR loses to Pop, Pop can still be promoted and the service stays up. Thresholds live in `configs/quality.yaml`; their rationale is in the experiment report. Each decision is written to `state/gate/<release_id>.json`, and its hash goes into the promotion log. Release directories are never modified after packaging.

## 7. Promotion and rollback

```sql
CREATE TABLE champion (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  release_id TEXT, version_counter INTEGER NOT NULL,
  previous_release_id TEXT, promoted_at_utc TEXT, actor TEXT, gate_decision_sha256 TEXT
);
```

- `promote(release_id, expected_version, execute=False)`: run the gate. If it fails, the result is `REJECTED`. Without `execute` the result is `DRY_RUN`. With `execute`, one transaction runs `UPDATE champion SET ..., version_counter = version_counter + 1 WHERE id = 1 AND version_counter = ?`. Zero rows updated means `CONFLICT`; otherwise `PROMOTED`.
- **Why a version counter and not the release ID**: after A -> B -> A the release ID is A again, so comparing IDs cannot see that anything changed (the ABA problem). The counter only grows, so a request holding a stale version always gets `CONFLICT`.
- `rollback` uses the same CAS. The target must have been promoted before and must still load. Status: `ROLLED_BACK`.
- Promotion only moves the pointer. The service picks up the new release after a restart, and `GET /ready` shows `loaded_release_id` to confirm it.

## 8. Serving and monitoring

- FastAPI loads the champion in `lifespan`, never at import. If there is no champion, `/ready` returns 503 `NO_CHAMPION`. If loading fails, it returns 503 with `BUNDLE_INVALID`, `USER_KEY_UNAVAILABLE` or `SMOKE_FAILED`, and **does not fall back to another release**. The service runs a single worker and has no hot reload.
- `GET /health`, `GET /ready`, `POST /v1/recommend` (`{"user_id", "top_k"}`). See [`schemas/openapi.v1.yaml`](schemas/openapi.v1.yaml).
- `user_id` must be non-blank and at most 128 characters; `top_k` is 1..50. Unknown users get the Pop fallback with `fallback_used=true`. Seen items are excluded, results are unique, ranks are contiguous, and scores are ranking scores, not probabilities. If no candidates remain, the response is 200 with `items=[]` and `empty_reason="NO_CANDIDATES"`.
- Errors use one `ApiError` shape: 422 `INVALID_ARGUMENT` (including malformed JSON), 503 `MODEL_NOT_READY`, 500 `INTERNAL_ERROR`.
- Every recommend request, errors included, writes an event to `state/events.db` with the HMAC user key (null for invalid arguments) and never the request body. If writing an event fails, the response still succeeds.
- `reviewpulse monitor summary` reports request count, 4xx/5xx, p50/p95 latency, fallback rate and empty rate per release and source. A group with fewer than 20 events is marked `INSUFFICIENT_DATA`. Online recommendation quality is always `UNAVAILABLE`, because there are no impressions or clicks.

## 9. Final test evaluation

`reviewpulse freeze-test` records the four config hashes and the selected run IDs in `reports/frozen_config.json`. `reviewpulse evaluate-test --run-id <id> --confirm-final` then refuses any run that is not frozen or already has a formal record, and appends one line to `reports/test_runs.log`. Corrections are append-only. CI checks that each frozen run has exactly one formal record and that the configs have not changed since freezing.

## 10. Scope: deliberately left out

This is a single-machine project built in five days. These parts are designed but not implemented:

| Left out | Why | What production would need |
|---|---|---|
| Deployment verification (desired vs. actual state) | One process on one machine; `/ready` is enough | `deploy verify` across replicas |
| Separate keys for bundles and telemetry | The dataset IDs are already anonymized; no multi-team boundary | Two keys with rotation |
| Drift monitoring and feedback retraining | No live traffic | Drift detection, a retraining state machine, and champion re-evaluation on the new split |
| Content features and fusion re-ranking | Out of scope for the first version | Text or item features for the many cold items |
| Library models (RecBole, LightGCN) | Compatibility risk; a hand-written BPR is easy to explain and is scored by the project's own evaluator | A new decision record if they are added |

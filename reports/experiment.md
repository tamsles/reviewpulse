# ReviewPulse Experiment Report

> Every number in this report comes from a real run on Amazon Reviews'23 `All_Beauty` (run date 2026-11-23). Each result carries its run_id and split_sha256. The synthetic fixture is used only for tests and CI and does not appear in any result table here.

## 1. Summary
- **Pop beats BPR on offline metrics.** On validation, NDCG@10 is 0.00842 for Pop and 0.00458 for BPR. On test it is 0.00797 for Pop and 0.00296 for BPR. The 95% bootstrap CI of the paired difference on validation is [-0.00645, -0.00149], which does not contain 0.
- The quality gate made **Pop the champion** (`rel-1fd01c0aa3d4`). BPR was rejected with `NOT_BETTER_THAN_POP`.
- The main cause is extreme sparsity: 92% of users have a single review, and most positive reviews in the evaluation windows are for new items that did not appear during training.
- This is a **single-machine** project. It demonstrates reproducible offline evaluation and an auditable release process. It does not claim online recommendation quality (see Section 8).

## 2. Data
| Item | Value |
|---|---|
| Source | Amazon Reviews'23, `All_Beauty`, review file 94,441,517 bytes, sha256 `ee00e668…e349` |
| License review | Done: no formal license; the maintainers say the data is mainly for research. The config field stays `pending` because the config was already frozen (see Section 8 and `reports/data-spike.md`). The repository contains only the download script and manifests, no data. |
| timestamp | Milliseconds (checked against real records during the spike, see `reports/data-spike.md`) |
| Raw / rejected / exact duplicates / final | 701,528 / 0 / 7,276 / **694,252** |
| Users / items (raw) | 631,986 / 112,565 |
| Positive | rating >= 4.0 (a "positive review", **not** a click or purchase) |
| Sparsity | 1.11 reviews per user on average; only 7.66% of users have >= 2 reviews |
| normalized_sha256 | `539f0a01631878c1d01456aad06447ada6d6ff0588ac3e6e84cddc9ee7801cd8` |

Running prepare twice on the same input gives the same `normalized_sha256` and the same parquet bytes. Shuffling the input row order gives the same result too.

## 3. Time split
A global time split, all windows half-open. No k-core or other filtering.

| Window | Range (UTC) | Interactions | Users | Items | Warm users | Cold users |
|---|---|---|---|---|---|---|
| train | < 2021-05-01 | 546,927 | 502,860 | 88,939 | — | — |
| valid | 2021-05-01 to 2022-01-01 | 72,169 | 68,368 | 26,069 | 5,251 | 63,117 |
| test | 2022-01-01 to 2023-10-01 | 75,156 | 71,123 | 21,978 | 4,077 | 67,046 |

`split_sha256 = 21a1d1c62badc5a2fa386f808e526273572108d14e621a28172a5ef2cf96def3`, `protocol_sha256 = 0f486b9cadedac9924ea78514f8a4d3ac349c53f61b01255b8031a7000da3efa`

The boundaries are explained in `reports/data-spike.md`: validation and test hold about 10% each, and `test_end` lies after the last record (2023-09-09), so no data is dropped.

## 4. Evaluation protocol
Let `C = train_end`. Full details and the hand-computed golden case are in [`docs/design.md`](../docs/design.md#3-offline-evaluation-protocol).
- **seen(u)**: **every** item u reviewed before C, including low ratings. All are excluded from recommendations.
- **eligible**: items with at least one review before C.
- **Ground truth** = positive reviews in the window, minus seen(u), intersected with eligible.
- **Valid user**: has reviews before C and non-empty ground truth.
- **Full ranking**: every user is ranked over `eligible - seen(u)`, **no sampled-negative evaluation**. Ties are broken by item_id ascending.
- **For test, seen(u) still stops at C**. Validation-window reviews are not added.
- Metrics: NDCG@10 (binary relevance) and Recall@10 (denominator |rel|), both averaged per user. Coverage@10 = distinct items in all top-10 lists / |eligible|.

The evaluator is tested against the hand-computed golden case. Offline evaluation and the API share one `rank()` function.

## 5. Results

### 5.1 Validation (used for model selection and gate thresholds)
| Model | run_id | NDCG@10 | Recall@10 | Coverage@10 | Valid users | Fallback users |
|---|---|---|---|---|---|---|
| **Pop** | `0711a659f7bf41afad75c74d2336ace7` | **0.008416** | **0.015787** | 0.000124 | 2,122 | 0 |
| BPR (selected) | `9c62b80a195642f2a2e9dd455c7e08a2` | 0.004583 | 0.008718 | 0.026372 | 2,122 | 377 |

### 5.2 Test (after freezing the config, **one run per model**)
| Model | run_id | NDCG@10 | Recall@10 | Coverage@10 | Valid users | Fallback users |
|---|---|---|---|---|---|---|
| **Pop** | `0711a659f7bf41afad75c74d2336ace7` | **0.007975** | **0.020264** | 0.000124 | 1,135 | 0 |
| BPR (selected) | `9c62b80a195642f2a2e9dd455c7e08a2` | 0.002961 | 0.007930 | 0.020407 | 1,135 | 236 |

Both tables use the same split, the same protocol and the same candidate rules. On test both models have `ground_truth_sha256` `aa7b3a03…49cf`.

### 5.3 All exclusion counts
| Count | validation | test |
|---|---|---|
| Eligible items | 88,939 | 88,939 |
| Valid users | 2,122 | 1,135 |
| Warm users with no ground truth (not averaged) | 3,129 | 2,942 |
| Ground truth excluded as already seen | 13 | 15 |
| Ground truth excluded as cold items (not eligible) | **16,369** | **26,223** |
| BPR users falling back to Pop (no positive review in train) | 377 | 236 |

In both windows the excluded cold-item ground truth far outnumbers the ground truth that is kept. This category has many new items and few returning users, which leaves collaborative filtering little to work with. Cold start is not part of the main evaluation (`cold_start_report: separate`, not implemented in this version).

## 6. BPR hyperparameter selection (validation only)
Hand-written PyTorch BPR with SparseAdam, batch 4096, seed 42, up to 50 epochs, early stopping on validation NDCG@10 (patience 5). The grid has 8 configs; together they took 316 seconds of training on CPU.

| run | dim | lr | reg | best / epochs run | train s | val NDCG@10 | val Recall@10 | val Coverage@10 |
|---|---|---|---|---|---|---|---|---|
| `9c62b80a` | 64 | 5e-3 | 1e-4 | 5 / 10 | 51 | **0.004583** | 0.008718 | 0.02637 |
| `a8c39510` | 64 | 5e-3 | 1e-5 | 5 / 10 | 50 | 0.004583 | 0.008718 | 0.02616 |
| `10eb40cd` | 32 | 5e-3 | 1e-5 | 5 / 10 | 42 | 0.003718 | 0.007540 | 0.02114 |
| `5f3a09b5` | 32 | 5e-3 | 1e-4 | 5 / 10 | 42 | 0.003718 | 0.007540 | 0.02131 |
| `b7956693` | 64 | 1e-3 | 1e-5 | 1 / 6 | 34 | 0.002960 | 0.005660 | 0.15109 |
| `21969b00` | 64 | 1e-3 | 1e-4 | 1 / 6 | 34 | 0.002960 | 0.005660 | 0.15109 |
| `6ecbf9b8` | 32 | 1e-3 | 1e-5 | 1 / 6 | 33 | 0.002960 | 0.005660 | 0.12428 |
| `9d81c4c8` | 32 | 1e-3 | 1e-4 | 1 / 6 | 30 | 0.002960 | 0.005660 | 0.12430 |

- **Selection rule**: highest validation NDCG@10 wins. `9c62b80a` and `a8c39510` have identical NDCG and Recall, so the tie is broken by higher coverage.
- **The four lr=1e-3 configs never started learning**: over 6 epochs the loss only went from 0.694 to 0.669 and validation NDCG did not move, so patience 5 stopped them. Broken down, the 0.002960 comes **entirely from the 377 users on the Pop fallback**; users the model knows contribute almost nothing. In other words, these BPR models were equivalent to random when they were stopped.
- **The four lr=5e-3 configs** peak at epoch 5. After that the loss keeps falling while validation drops: overfitting.
- Per-user breakdown (validation, selected BPR): on the 1,745 users BPR knows, NDCG is 0.00197 for BPR and 0.00663 for Pop. On the 377 fallback users the two are identical.
- Paired comparison (validation): BPR - Pop = -0.00383, paired standard error 0.00129, 95% CI from 2,000 bootstrap samples [-0.00645, -0.00149]. Pop's NDCG standard error is 0.00153. The expected NDCG of a random ranking (|rel|=1) is about 0.000051.
- No bootstrap was run on test, because that would mean looking at test again. The test gap (-0.0050) points the same way as on validation.

## 7. Release, gate and serving

### 7.1 Gate thresholds (`configs/quality.yaml`, set from validation numbers)
| Threshold | Value | Rationale |
|---|---|---|
| `min_abs_ndcg` | 0.004 | About 80x a random ranking and about Pop minus 2.9 standard errors; catches bundles with a broken model or wrong mappings |
| `min_improvement` | 0.0025 | About 2 paired standard errors; a non-Pop model must clearly beat noise |
| `max_regression` | 0.0 | Evaluation is deterministic; on the same split a new model may not be worse than the champion |

### 7.2 Gate results
| release | Model | build | Result |
|---|---|---|---|
| `rel-1fd01c0aa3d4` | Pop `0711a659` | `741fa16`, clean | **PASSED**, promoted to champion (version 1) |
| `rel-afe1b95456a0` | BPR `9c62b80a` | `0338be2`, clean | REJECTED: `NOT_BETTER_THAN_POP` (0.004583 <= 0.008416 + 0.0025) |
| `rel-fb924c045eab` | Pop `1ac6483d` | `0539759`, **dirty** | REJECTED: `DIRTY_BUILD` |

The dirty build really happened. I redirected training output into a directory inside the repository; the shell created that file before training started, so the run was recorded as dirty. Pop was then retrained on a clean worktree, and every metric was bit-for-bit identical. The dirty release is kept as a real example of a gate rejection.

### 7.3 Serving (local, Windows, one worker, champion `rel-1fd01c0aa3d4`)
| Item | Value |
|---|---|
| Startup to ready (Docker) | about 10 s (loading the 47 MB Pop release; most of it is the seen sets for 500k user keys) |
| Server-side latency (`monitor summary`, 200 requests, top_k=10) | p50 2.05 ms / **p95 2.42 ms** |
| Client-side latency (including local HTTP round trip) | p50 7.05 ms / p95 7.87 ms |
| Privacy scan | releases, event store and service logs checked against all 631,986 real user_ids: 0 hits |

Training time: Pop 1.07 s; selected BPR 50.6 s (10 epochs, including a validation evaluation every epoch); prepare about 12 s; split about 3 s.

## 8. Limitations
1. **A rated review is not a purchase.** "Positive" here means a review with rating >= 4, not a click, add-to-cart or purchase.
2. **No review is not dislike.** BPR negatives are only "not observed".
3. **Offline NDCG is not online CTR.** The system has no impression or click data, so online quality in `monitor summary` is always `UNAVAILABLE`.
4. **BPR does worse than Pop.** On this data the personalized model brings no offline gain. The conclusion holds only for this category and this protocol and does not mean "BPR does not work".
5. **Small samples.** Pop's NDCG standard error on validation is about 0.0015 (2,122 users). Test has only 1,135 valid users and is noisier (no standard error computed on test). Small differences are not conclusive.
6. **Grid limits.** With lr=1e-3, patience 5 was not enough; the models stopped before the metric started to move. The grid and patience were not changed after the fact, so BPR's ceiling may be underestimated. However, lr=5e-3 shows that even when training does work, it overfits quickly and its peak is still below Pop.
7. **Cold start is not handled.** Excluded cold-item ground truth is about 7.7x the number of valid users on validation (16,369 / 2,122) and about 23x on test (26,223 / 1,135). This needs content features and fusion re-ranking, which are out of scope.
8. **Single-machine project.** Deployment verification, separate keys, drift monitoring and feedback retraining were left out on purpose; see [`docs/design.md`](../docs/design.md#10-scope-deliberately-left-out).
9. **Data license**: Amazon Reviews'23 has no formal license; the maintainers say it is mainly for research (see `reports/data-spike.md`). This is a non-commercial project and does not redistribute raw data or review text. The review happened after the config was frozen, so `license_review_status` in `configs/source.yaml` and the manifest still reads `pending`. It was left unchanged so the frozen config hashes stay valid.
10. **A flaw in the hash records (corrected)**: the `evaluation_sha256` in the two formal records was the hash of the CRLF file written on Windows. Git stores the LF version, so a fresh clone did not match the records. Both formal evaluations ran on the same code; the fix came afterwards, and since then all JSON is written with LF. The metrics are unaffected.
   - **How it was corrected**: no existing record was edited. One record per run was appended to `test_runs.log` with `"formal": false, "type": "evaluation_sha256_correction"`, stating the old hash, the new hash and the reason. A correction is only accepted if converting the LF file back to CRLF reproduces the original hash exactly, which proves the content did not change. Any other change is rejected (covered by a counter-example test).
   - **How it is checked**: `tests/test_reports_integrity.py` runs in CI and checks that every formal record (following the correction chain) matches the file in the repository, that each frozen run has exactly one formal record, that the configs have not changed since freezing, and that the report files use LF.
   - Mapping:

| run | Recorded in log (CRLF) | File in Git (LF) |
|---|---|---|
| `0711a659` | `731a2e0c…7a92` | `235f8597…7f66` |
| `9c62b80a` | `cf290de1…1a69` | `53c30a18…4425` |

## 9. Reproduction
```bash
python scripts/download_data.py --category All_Beauty
export RP_USER_KEY=...            # any secret of >= 16 chars; keep it for packaging/serving
reviewpulse data prepare && reviewpulse data split
reviewpulse train --model Pop
reviewpulse train --model BPR --grid
reviewpulse package --run-id <run_id>
reviewpulse gate --release-id <release_id>
reviewpulse promote --release-id <release_id> --expected-version 0 --execute
```
A different `RP_USER_KEY` changes every `user_key` and therefore the release_id, but none of the metrics. The test evaluation is frozen and has already been run (`reports/frozen_config.json`, `reports/test_runs.log`) and should not be run again.

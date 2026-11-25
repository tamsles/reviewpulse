# Data Spike (2026-11-23)

All numbers are output of `scripts/data_spike.py` on the real file, counted over **raw records** (before deduplication).

## Source
| Item | Value |
|---|---|
| Dataset | Amazon Reviews'23, category `All_Beauty` |
| URL | https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/All_Beauty.jsonl.gz |
| File size | 94,441,517 bytes (gzip) |
| sha256 | `ee00e66835567c3f12fde6a482f8d7055c22cac2d0924f677263affdd8a0e349` |
| License review | Done (2026-11-23): **no formal license**; the maintainers say the data is mainly for research. The config field still reads `pending`; see the next section. |

Reproduce: `python scripts/download_data.py --category All_Beauty`, then `python scripts/data_spike.py --raw data/raw/All_Beauty.jsonl.gz`.

## License review (added 2026-11-23)
- **Official page** <https://amazon-reviews-2023.github.io/>: no license or terms of use, only a paper citation. The footer "Copyright © 2024, McAuley Lab" appears to cover the website itself.
- **Maintainer statement** (Hugging Face discussion, 2024-03-08, <https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/discussions/1>): the lab says it cannot assign a license or dictate terms of use; the data is available primarily for research, and users are responsible for ethical and legal compliance. In the same thread (2024-12) the maintainer saw no issue with use in a non-commercial thesis.
- Third-party pages claiming CC BY-NC 4.0 or CC BY-SA 4.0 are **not** official and are not relied on. Rights in the review text itself may also involve Amazon's terms.
- **How this project uses it**: a non-commercial portfolio project. Raw data and review text are neither committed nor redistributed. `user_id` is HMAC-keyed in every artifact. Only aggregate statistics and offline metrics are published. The dataset is cited as Hou et al. 2024 (arXiv:2403.03952).
- **Why `configs/source.yaml` still says `pending`**: the review happened after the test configuration was frozen (`reports/frozen_config.json`, 2026-11-23T08:16:24Z). `source.yaml` is one of the four frozen configs; its hash was recorded at freeze time and `tests/test_reports_integrity.py` checks it has not changed. To keep the freeze intact, the value stays `pending` in the config and manifest, and this section is the record of the review. The field only goes into the manifest and affects neither `normalized_sha256`, the split nor any metric.

## Field check (first 1,000 rows)
- Fields: `rating` (float), `title`, `text`, `images`, `asin`, `parent_asin` (str), `user_id` (str), `timestamp` (int), `helpful_vote`, `verified_purchase`.
- `user_id`: always 28 characters (an anonymized ID). `parent_asin`: always 10 characters, equal to `asin` in 93.1% of rows; `parent_asin` is used as `item_id`.
- **timestamp unit is milliseconds**: all values have 13 digits. Examples:

| timestamp | UTC |
|---|---|
| 1588687728923 | 2020-05-05T14:08:48.923Z |
| 1588615855070 | 2020-05-04T18:10:55.070Z |
| 1589665266052 | 2020-05-16T21:41:06.052Z |
| 1643393630220 | 2022-01-28T18:13:50.220Z |
| 1609322563534 | 2020-12-30T10:02:43.534Z |

## Size
| Metric | Value |
|---|---|
| Reviews | 701,528 |
| Users | 631,986 |
| Items | 112,565 |
| Nulls in user / item / rating / timestamp | 0 / 0 / 0 / 0 |
| Exact duplicates (user, item, timestamp, rating) | 7,276 |
| Time range | 2000-11-01 .. 2023-09-09 |
| Positive (>= 4) share | 71.29% |
| Reviews per user | mean 1.11; 7.66% of users have >= 2; max 165 |

Rating distribution: 1 star 102,080; 2 stars 43,034; 3 stars 56,307; 4 stars 79,381; 5 stars 420,726.

Reviews per year: 2015: 36,239; 2016: 62,747; 2017: 68,035; 2018: 72,707; 2019: 99,120; 2020: 126,753; 2021: 124,897; 2022: 61,581; 2023: 14,405. From mid-2021 the monthly count falls steadily, and from 2023-04 there are fewer than 2,000 reviews a month. The full monthly table is in the script output.

## Split boundaries
Time quantiles: 80% at 2021-05-24, 90% at 2022-01-26. "Valid users" below follow the evaluation protocol: any review in train, and a non-empty `positive - seen ∩ eligible` in the window. seen stops at `train_end`.

| train_end | valid_end | test_end | train | valid | test | valid% | test% | valid users (valid) | valid users (test) | eligible items |
|---|---|---|---|---|---|---|---|---|---|---|
| **2021-05-01** | **2022-01-01** | **2023-10-01** | 552,552 | 72,990 | 75,986 | 10.40 | 10.83 | **2,122** | **1,135** | 88,939 |
| 2021-03-01 | 2021-11-01 | 2023-10-01 | 525,591 | 86,424 | 89,513 | 12.32 | 12.76 | 2,193 | 1,206 | 84,433 |
| 2020-11-01 | 2021-08-01 | 2023-10-01 | 478,784 | 107,469 | 115,275 | 15.32 | 16.43 | 2,351 | 1,256 | 76,310 |
| 2021-01-01 | 2021-07-01 | 2022-01-01 | 500,645 | 74,599 | 50,298 | 10.63 | 7.17 | 2,002 | 775 | 80,187 |

**Chosen (2026-11-23)**: `train_end=2021-05-01`, `valid_end=2022-01-01`, `test_end=2023-10-01`.
- Validation and test each hold about 10% of interactions.
- `test_end` lies after the last record (2023-09-09), so no data is dropped.
- Wider windows barely add valid users (even the 15% option has only 2,351) and shrink train, so they were not used.

## Risks and limitations (carried into the experiment report)
1. **Few valid users**: 2,122 on validation, at the low end of "a few thousand"; only 1,135 on test, so metric noise is large and small differences are not conclusive.
2. **Extreme user sparsity**: 92% of users have one review. Collaborative filtering (BPR) has little signal and is **likely to lose to Pop**. That will be reported as is.
3. Uneven time distribution: the test window spans 21 months, but most reviews fall in the first few.
4. A rated review is not a purchase; no review is not dislike.

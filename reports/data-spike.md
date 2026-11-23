# T01 数据 Spike（2026-11-23）

全部数字来自 `scripts/data_spike.py` 对真实文件的输出，统计口径是**原始记录**（还没去重）。

## 数据源
| 项目 | 值 |
|---|---|
| 数据集 | Amazon Reviews'23，品类 `All_Beauty` |
| URL | https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/All_Beauty.jsonl.gz |
| 文件大小 | 94,441,517 字节（gzip） |
| sha256 | `ee00e66835567c3f12fde6a482f8d7055c22cac2d0924f677263affdd8a0e349` |
| 许可审核 | 已核对（2026-11-23）：**没有正式许可证**，维护者表示主要供研究用途。配置字段仍为 `pending`，原因见下节 |

复现：`python scripts/download_data.py --category All_Beauty`，然后 `python scripts/data_spike.py --raw data/raw/All_Beauty.jsonl.gz`

## 许可核对（2026-11-23 补记）
- **官方页面** <https://amazon-reviews-2023.github.io/>：没有写许可证或使用条款，只提供了论文引用。页脚的 "Copyright © 2024, McAuley Lab" 看起来是网站本身的版权声明。
- **维护者说明**（Hugging Face，hyp1231，2024-03-08，<https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/discussions/1>）：实验室 "not in a position to assign a license to this dataset or dictate the terms of its usage"，数据 "available primarily for research purposes"，使用者自行负责遵守伦理和法律规范。同一帖中（2024-12），维护者回复非商业毕业论文的使用 "I see no issues"。
- 第三方页面声称的 CC BY-NC 4.0 / CC BY-SA 4.0 都**不是**官方说法，不作为依据。评论内容本身的权利可能还涉及 Amazon 的条款。
- **本项目的用法**：非商业求职作品；不提交、不再分发原始数据和评论原文；user_id 在所有产物里都是 HMAC 后的值；只公开统计数字和离线指标。使用时引用 Hou et al. 2024（arXiv:2403.03952）。
- **为什么 `configs/source.yaml` 仍写 `pending`**：核对是在 test 配置冻结（`reports/frozen_config.json`，2026-11-23T08:16:24Z）之后做的。`source.yaml` 属于冻结的 4 个配置之一，冻结时记录了它的哈希，`tests/test_reports_integrity.py` 会检查它没有变化。为了不破坏冻结，配置和 manifest 里的值保持 `pending`，以本节作为核对结论的记录。这个字段只写进 manifest，不影响 `normalized_sha256`、切分或任何指标。

## 字段核对（前 1000 行）
- 字段：`rating`(float)、`title`、`text`、`images`、`asin`、`parent_asin`(str)、`user_id`(str)、`timestamp`(int)、`helpful_vote`、`verified_purchase`
- `user_id`：全部 28 个字符（是匿名 ID）。`parent_asin`：全部 10 个字符，其中 93.1% 和 `asin` 相同，按规格用 `parent_asin` 作为 `item_id`
- **timestamp 单位是 ms**：数值全部 13 位。换算示例：

| timestamp | UTC |
|---|---|
| 1588687728923 | 2020-05-05T14:08:48.923Z |
| 1588615855070 | 2020-05-04T18:10:55.070Z |
| 1589665266052 | 2020-05-16T21:41:06.052Z |
| 1643393630220 | 2022-01-28T18:13:50.220Z |
| 1609322563534 | 2020-12-30T10:02:43.534Z |

## 规模
| 指标 | 值 |
|---|---|
| 评论数 | 701,528 |
| 用户数 | 631,986 |
| 商品数 | 112,565 |
| user/item/rating/timestamp 空值 | 0 / 0 / 0 / 0 |
| 精确重复（user, item, timestamp, rating） | 7,276 |
| 时间范围 | 2000-11-01 .. 2023-09-09 |
| 正向（≥4）占比 | 71.29% |
| 每用户评论数 | 平均 1.11；≥2 条的用户占 7.66%；最多 165 条 |

评分分布：1 分 102,080；2 分 43,034；3 分 56,307；4 分 79,381；5 分 420,726。

按年评论数：2015 年 36,239 条，2016 年 62,747，2017 年 68,035，2018 年 72,707，2019 年 99,120，2020 年 126,753，2021 年 124,897，2022 年 61,581，2023 年 14,405。2021 年中以后每月的评论数一路下降，2023-04 以后每月不到 2,000 条。完整的按月表在脚本输出里。

## 切分边界
时间分位数：80% 在 2021-05-24，90% 在 2022-01-26。下表的"有效用户"按 02 的口径计算：train 中有任意评论，且在窗口内有 `正向 − seen ∩ eligible` 非空的商品。seen 只算到 `train_end`。

| train_end | valid_end | test_end | train | valid | test | valid% | test% | valid 有效用户 | test 有效用户 | eligible 商品 |
|---|---|---|---|---|---|---|---|---|---|---|
| **2021-05-01** | **2022-01-01** | **2023-10-01** | 552,552 | 72,990 | 75,986 | 10.40 | 10.83 | **2,122** | **1,135** | 88,939 |
| 2021-03-01 | 2021-11-01 | 2023-10-01 | 525,591 | 86,424 | 89,513 | 12.32 | 12.76 | 2,193 | 1,206 | 84,433 |
| 2020-11-01 | 2021-08-01 | 2023-10-01 | 478,784 | 107,469 | 115,275 | 15.32 | 16.43 | 2,351 | 1,256 | 76,310 |
| 2021-01-01 | 2021-07-01 | 2022-01-01 | 500,645 | 74,599 | 50,298 | 10.63 | 7.17 | 2,002 | 775 | 80,187 |

**选定（2026-11-23 用户确认）**：`train_end=2021-05-01`、`valid_end=2022-01-01`、`test_end=2023-10-01`。
- valid 和 test 各占约 10%，符合 SPLIT-01。
- `test_end` 放在最后一条记录（2023-09-09）之后，所以不会丢数据。
- 放宽窗口几乎不增加有效用户（15% 的方案也只有 2,351 人），反而让 train 变小，所以不放宽。

## 风险与局限（会写进 experiment.md）
1. **有效用户偏少**：valid 2,122 人，刚好在"几千"的下限；test 只有 1,135 人，指标的抽样噪声较大，小差距不能下结论。
2. **用户极度稀疏**：92% 的用户只有 1 条评论，协同过滤（BPR）能利用的信号很少，**很可能不如 Pop**。按 C-12 会照实报告。
3. 时间上的分布不均：test 窗口有 21 个月，但评论集中在前面的几个月。
4. 评分评论 ≠ 购买；未评论 ≠ 不喜欢（C-12）。

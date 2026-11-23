# 01 数据契约与时间切分

## DATA-01 数据源与品类
- Amazon Reviews'23，**只选一个小品类**。Day 1 的数据 Spike（T01）决定：候选为评论数在几十万量级的品类（例如 `All_Beauty`），实际规模以下载后统计为准。目标：笔记本 CPU 上 prepare + BPR 训练各在 10 分钟内。
- 只提交下载脚本、`configs/source.yaml` 和数据 manifest，不提交原始数据。许可条款状态写进 manifest 的 `license_review_status`。

## DATA-02 原始字段
| 字段 | 约束 |
|---|---|
| `user_id` | 去首尾空白后非空 |
| `parent_asin` | 非空，作为 `item_id` |
| `rating` | 有限值，1.0 ≤ rating ≤ 5.0 |
| `timestamp` | 单位在 `source.yaml` 中**显式写明**（ms / s），Spike 时用真实记录核对；未写明则报错 |
| `text` | 可空；P0 推荐不用，情感加分项用 |

## DATA-03 规范化交互表（parquet）
```text
interaction_id  str   # sha256(user_id|item_id|event_time|rating) 前 16 位
user_id         str   # 仅离线环境
item_id         str
event_time_utc  datetime[UTC]
rating          float
is_positive     bool  # rating >= positive_rating_threshold（默认 4.0），不是点击/购买标签
source          str   # 固定 "amazon_review"
source_snapshot_id str  # = 数据 manifest 的 normalized_sha256 前 16 位
```
与 `schemas/normalized-interaction.v1.schema.json` 一致。
- 拒绝：缺必需列、非法评分、时间无法转换 → `DataContractError`，不产出文件。
- 精确重复 `(user_id, item_id, event_time_utc, rating)` 只保留一条，计数写进 manifest。
- 同一用户对同一商品多次评论：交互表保留全部；已见 / 真值按集合计算，天然去重。

## DATA-04 数据 manifest（`data/processed/manifest.json`）
`category`、`source_url`、`source_sha256`、`normalized_sha256`、`timestamp_unit`、`positive_rating_threshold`、`record_counts`（原始、拒绝、去重、最终）、`license_review_status`、`created_at_utc`。
同样的输入和配置跑两次，`normalized_sha256` 必须相同（排序后再写，避免行顺序影响哈希）。

## SPLIT-01 全局时间切分
在 `configs/split.yaml` 写死 UTC 边界 `train_end < valid_end < test_end`：
- train：`event_time < train_end`
- valid：`train_end <= event_time < valid_end`
- test：`valid_end <= event_time < test_end`

边界选法（T01 决定）：让 valid 与 test 各含约 10% 的交互，并保证 valid 中至少有几千个 `valid_user`（定义见 02）。

## SPLIT-02 过滤
- 若要做 k-core 等过滤，**只根据 train 窗口**统计决定；不得用 valid/test 行为筛用户。
- P0 默认不做 k-core，冷用户 / 冷商品按 02 的口径剔除并计数。

## SPLIT-03 split manifest（`data/split/manifest.json`）
三个边界、各窗口交互数、用户数、商品数、`split_sha256`（对三个窗口的 `interaction_id` 排序列表求哈希）、`normalized_sha256`。

## 验收
- `DATA-T01` 重复记录去重结果确定
- `DATA-T02` 非法评分 / 非法时间被拒绝
- `DATA-T03` 未声明 timestamp 单位时报错
- `DATA-T04` 同一输入两次 prepare 哈希一致；打乱行顺序哈希也一致
- `TE-T01` 往原始数据里加一条 test 期之后的评论，train 窗口文件字节不变
- `TE-T03` 只在 test 出现的用户不进入 warm 用户统计

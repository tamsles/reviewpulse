# 02 离线评估协议（沿用 v1.1 ADR-0007 第 1–7 条）

这一章是整个项目最值得在面试里讲的部分，原样沿用 v1.1 已批准的口径。

## EVAL-01 定义（记 `C = train_end`，评估窗口为 `W`）
1. **已见** `seen(u)`：`u` 在 `C` 之前评论过的**任何评分**的商品（含低分）。推荐时全部排除。
2. **可推荐** `eligible`：在 `C` 之前至少有一条评论（任何评分）的商品。
3. **真值** `rel(u, W)` = { `u` 在 `W` 内正向评价的商品 } − `seen(u)`，再与 `eligible` 取交集，同一商品只计一次。
   - 因为在 seen 里被剔除的计入 `excluded_seen_relevance_count`；
   - 因为不在 eligible（冷商品）被剔除的计入 `excluded_cold_relevance_count`。
4. **有效用户** `valid_user`：在 `C` 之前有任何评论，且 `|rel| >= 1`。`|rel| = 0` 的用户不进平均，计入 `no_relevance_user_count`。
5. 模型不认识的有效用户（例如 BPR 映射外）按 Pop 打分，计入 `fallback_user_count`。
6. 测试时已见集合仍只到 `C`，不加入 valid 窗口的评论（报告中声明这一点）。

## EVAL-02 指标
- `ndcg_at_10`：二元相关，`DCG = sum(1/log2(rank+1))`，IDCG 取前 `min(|rel|, 10)` 个位置；用户级平均。
- `recall_at_10 = hits / |rel|`；用户级平均。
- `coverage_at_10 = |所有用户 Top-10 中出现的不同商品| / |eligible|`。
- 并列分数按 `item_id` 升序打破。
- 全量排序：每个用户在 `eligible − seen(u)` 上排序，不做负采样评估。

## EVAL-03 评估 manifest（`evaluation.json`，schema 见 `schemas/evaluation-manifest.v1.2.schema.json`）
必含：`run_id`、`model_kind`、`eval_split`（validation / test）、`split_sha256`、`protocol_sha256`（对 `split.yaml` 中 evaluation 段做规范化 JSON 后求哈希）、`ground_truth_sha256`、`metrics`、`counts`（上面所有计数 + `eligible_item_count`）、`protocol_version`、`created_at_utc`。

## EVAL-04 Golden Case（必须写成测试）
数据：`examples/synthetic_reviews.jsonl`；切分：`examples/split.example.yaml`（`train_end=2023-01-01`、`valid_end=2023-04-01`、`test_end=2023-07-01`）。以下数字已用脚本手算核对。

**train 窗口**：u1: a(5), b(4)；u2: a(4), c(2)；u3: c(5), d(1)
- `seen`：u1={a,b}，u2={a,c}，u3={c,d}；`eligible`={a,b,c,d}
- Pop 分数（C 前正向评价的去重用户数）：a=2, b=1, c=1, d=0 → 排序 a, b, c, d（b、c 并列按 id）

**validation**：
| 用户 | rel | Pop 候选（去掉 seen） | NDCG@10 | Recall@10 |
|---|---|---|---|---|
| u1 | {c} | c, d | 1.0 | 1.0 |
| u2 | {b} | b, d | 1.0 | 1.0 |
| u3 | {}（a 只给了 3 分） | — | 不计入 | 不计入 |

→ Pop：`ndcg_at_10 = 1.0`，`recall_at_10 = 1.0`，`valid_user_count = 2`，`no_relevance_user_count = 1`，两类剔除计数都为 0。

**test**：
- u1 给 a 打 5 分 → a 在 seen 中，`excluded_seen_relevance_count = 1`；u1 给 e 打 2 分 → 非正向
- u4 给 e 打 4 分 → e 不在 eligible，`excluded_cold_relevance_count = 1`（u4 也不是有效用户）
- u2 给 d 打 5 分 → rel={d}，Pop 候选 b, d，命中在第 2 位

→ Pop：`ndcg_at_10 = 1/log2(3) ≈ 0.6309`，`recall_at_10 = 1.0`，`valid_user_count = 1`。

## 验收
- `TE-T07` 上面的 Golden Case 数字逐项相等（浮点误差 1e-9）
- `TE-T08` 训练期打过低分的商品不会出现在候选中（u2 的 c、u3 的 d）
- `EVAL-T01` `|rel|=0` 的用户不进平均且被计数
- `EVAL-T02` 指标为 NaN / 有效用户数为 0 时评估器报错，而不是返回 0 分

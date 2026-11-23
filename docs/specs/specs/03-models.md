# 03 模型：Pop 与 BPR

## MODEL-01 统一接口
```python
class Recommender(Protocol):
    kind: str  # "Pop" | "BPR"
    def score_items(self, user_key: str, item_ids: list[str]) -> list[float]: ...
    def knows_user(self, user_key: str) -> bool: ...
```
过滤已见、截断 Top-K、并列打破、Pop 回退统一在一个 `rank()` 函数里做，评估器和 API 共用，保证离线与线上同一套逻辑。

## MODEL-02 Pop
- 分数 = `C` 之前对该商品正向评价的去重用户数；只有低分评论的商品分数 0，但仍可推荐。
- 确定性，无训练参数；同时作为冷启动回退和门禁基线。

## MODEL-03 BPR（自写 PyTorch，ADR-0009 D2）
- 结构：用户 embedding `P`、商品 embedding `Q`，分数 `s(u,i) = P[u] · Q[i]`。
- 训练样本：train 窗口中 `is_positive=true` 的 (u, i)；每个样本均匀采样一个**该用户在 train 中没有任何评论**的商品 j 作为负样本。
- 损失：`-mean(log(sigmoid(s(u,i) - s(u,j)))) + reg * (||P[u]||^2 + ||Q[i]||^2 + ||Q[j]||^2)`。
- 映射：只有 train 中有正向评价的用户进 BPR 用户映射；商品映射覆盖整个 `eligible`（只有低分评论的商品也有 embedding，随机初始化）。
- 超参（只在 validation 上选，网格小）：`dim ∈ {32, 64}`、`lr ∈ {1e-3, 5e-3}`、`reg ∈ {1e-5, 1e-4}`、epoch 上限 50，按 validation NDCG@10 早停（patience 5）。网格最多 8 组。
- 固定 seed；CPU 能跑完。若 Day 2 结束时还跑不完网格，就只跑默认一组 `dim=64, lr=1e-3, reg=1e-4`，在报告中写明。
- 说明：负样本只是"未观察"，不代表用户不喜欢（C-12）。

## MODEL-04 训练输出
每次训练（包括 Pop）在 MLflow 记一个 run：
- params：模型超参、seed、`split_sha256`、`protocol_sha256`、`normalized_sha256`、git commit、git dirty
- metrics：`rec/val/ndcg_at_10`、`rec/val/recall_at_10`、`rec/val/coverage_at_10`、训练秒数
- artifacts：模型文件（`model.pt` 或 `pop.json`）、`item_mapping.json`、`user_mapping.json`（键为 `user_key`）、`evaluation.json`
- 训练窗口终点 = `train_end`；**门禁之后不重训替换**（沿用 v1.1 ADR-0006）

## 验收
- `REC-T01` Pop 分数与排序等于 Golden Case
- `REC-T02` BPR 在合成 fixture 上能训练 2 个 epoch 且损失有限
- `REC-T03` 保存 → 新进程加载 → 同一用户 Top-10 完全一致
- `REC-T04` 结果不含已见商品、不重复、排名连续
- `REC-T05` BPR 不认识的用户走 Pop 并标记 fallback
- `REC-T06` 模型输出 NaN / Inf 时报错

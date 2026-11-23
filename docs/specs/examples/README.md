# 合成示例

全部为人工合成，**不是**真实模型输出或实验结果。

| 文件 | 说明 | 相对 v1.1 |
|---|---|---|
| `synthetic_reviews.jsonl` | 13 条合成评论，直接复制到 `tests/fixtures/`；Golden Case 见 `specs/02-evaluation.md` | 不变 |
| `source.example.yaml` | 数据源配置形状；品类由 Day 1 Spike 决定 | 不变 |
| `split.example.yaml` | 时间边界与评估口径；合成数据用这组日期 | 不变 |
| `recommend-request.example.json` / `recommend-response.example.json` | u1 的已见为 a、b，候选只有 c、d（`candidate_count=2`）；分数是示意值 | 不变 |
| `champion-state.example.json` | 尚无 Champion 时的初始状态（`version_counter=0`） | 不变 |
| `quality.example.yaml` | 门禁配置形状，阈值为 null，Day 2 根据真实数字填 | 简化 |
| `release_manifest.example.json` | Release manifest 形状，全 0 哈希为占位，不能通过加载校验 | 简化（1.2） |
| `event.example.json` | 脱敏事件，合成来源 | 不变（仅日期） |

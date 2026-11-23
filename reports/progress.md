# 进度记录

## Day 1（实际日期 2026-11-23（一），比计划晚一天）

| Task | 状态 | 证据 |
|---|---|---|
| T00 仓库骨架 | 已实现 / 已测试 | pyproject、ruff、pytest、骨架、fixture；`.gitattributes` 统一 LF |
| T01 数据 Spike | 已完成 | `reports/data-spike.md`；边界 2021-05-01 / 2022-01-01 / 2023-10-01 已由用户确认 |
| T02 contracts + prepare | 已实现 / 已测试 | DATA-T01..04 通过；真实数据跑两次 prepare，哈希一致 |
| T03 split | 已实现 / 已测试 | TE-T01、TE-T03 通过；真实数据跑两次 split，哈希一致 |

### 验证命令
- `ruff check .`、`ruff format --check .` → exit 0
- `pytest` → 52 passed，exit 0
- `reviewpulse data prepare --source configs/source.yaml` ×2 → exit 0，每次约 12 秒
- `reviewpulse data split --split configs/split.yaml` ×2 → exit 0，每次约 3 秒

### 真实数据数字（All_Beauty）
| 项目 | 值 |
|---|---|
| 原始 / 去重 / 拒绝 / 最终 | 701,528 / 7,276 / 0 / 694,252 |
| normalized_sha256 | `539f0a01631878c1d01456aad06447ada6d6ff0588ac3e6e84cddc9ee7801cd8` |
| split_sha256 | `21a1d1c62badc5a2fa386f808e526273572108d14e621a28172a5ef2cf96def3` |

| 窗口 | 交互 | 用户 | 商品 | warm 用户 | cold 用户 |
|---|---|---|---|---|---|
| train（< 2021-05-01） | 546,927 | 502,860 | 88,939 | — | — |
| valid（< 2022-01-01） | 72,169 | 68,368 | 26,069 | 5,251 | 63,117 |
| test（< 2023-10-01） | 75,156 | 71,123 | 21,978 | 4,077 | 67,046 |

Spike 用原始数据估算的有效用户数：valid 约 2,122，test 约 1,135。准确的数字以 Day 2 评估器的输出为准。

### 设计决定（已实现）
- `normalized_sha256` 对排好序的行做规范化文本哈希（不含 `source_snapshot_id`），不对 parquet 字节求哈希，避免循环依赖，也不受库版本影响。
- 窗口文件不含 `source_snapshot_id` 列，这样 TE-T01 才能做到"注入未来评论后 train 字节不变"；数据血缘记录在 split manifest 的 `normalized_sha256` 里。
- 只要有一行不合法就整批报错，`rejected` 恒为 0。
- warm 用户的定义：`train_end` 之前有任意评论。split manifest 的 valid 和 test 段带 `warm_users` 和 `cold_users`。

### 未完成 / 未验证
- 没有在 Linux 或干净 clone 上验证（Day 4 的 CI 会验证）。
- 许可审核状态还是 `pending`。

### 明天第一件事
Day 2 的 T04：`rank()` + 评估器 + Golden Case 测试（TE-T07/T08、EVAL-T01/T02）。日程整体顺延一天：Day 2 = 11/24，……，Day 5 = 11/27。

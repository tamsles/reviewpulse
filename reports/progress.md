# 进度记录

## Day 1（实际日期 2026-11-23，比计划晚一天）

| Task | 状态 | 证据 |
|---|---|---|
| T00 仓库骨架 | 已实现 / 已测试 | `pytest` 3 passed，`ruff check` 通过 |
| T01 数据 Spike | 已完成 | `reports/data-spike.md`；边界 2021-05-01 / 2022-01-01 / 2023-10-01 已由用户确认 |
| T02 contracts + prepare | 已实现 / 已测试 | DATA-T01..04 通过；真实数据 prepare 两次 `normalized_sha256=539f0a01…1cd8` 一致，原始 701,528 → 去重 7,276 → 最终 694,252，约 12 秒 |
| T03 split | 未开始 | |

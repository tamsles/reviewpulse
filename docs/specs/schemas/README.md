# 机器可读接口契约（v1.2）

| 文件 | 说明 | 相对 v1.1 |
|---|---|---|
| `openapi.v1.yaml` | `/health`、`/ready`、`/v1/recommend` | 不变 |
| `recommend-request.v1.schema.json` | 推荐请求 | 不变 |
| `normalized-interaction.v1.schema.json` | 规范化交互（见 01 DATA-03） | 不变 |
| `monitoring-event.v1.schema.json` | 推荐请求事件 | `source` 去掉 `offline_replay` |
| `champion-state.v1.schema.json` | Champion 单行表导出形态 | 不变 |
| `release-manifest.v1.2.schema.json` | 简化的 Release manifest | **新**，取代 v1.1 版本 |
| `evaluation-manifest.v1.2.schema.json` | 简化的推荐评估 manifest | **新**，取代 v1.1 版本 |

v1.1 的 `deployment-record.v1.schema.json` 不再使用（ADR-0009 D4）。

Schema 只校验结构；哈希内容、映射维度、密钥指纹、端到端推理由 `release/loader.py` 和测试负责。

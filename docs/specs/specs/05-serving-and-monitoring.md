# 05 推荐 API 与监控

接口契约：`schemas/openapi.v1.yaml`（从 v1.1 原样复制，不变）。

## API-01 启动
- import 模块不读 champion、不加载模型；用 FastAPI `lifespan` 加载。
- 启动时读 champion 指针：
  - 无 champion → 能启动，`/ready` 503 `reason=NO_CHAMPION`
  - 加载失败 → `/ready` 503，`reason` = `BUNDLE_INVALID`、`USER_KEY_UNAVAILABLE` 或 `SMOKE_FAILED`；加载过程中为 `LOADING`；**不**改用其他 release
  - 成功 → 记下 `loaded_release_id`，`/ready` 200
- 单 worker 运行（`uvicorn --workers 1`）。没有 HTTP reload 接口。

## API-02 接口
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 200 `{"status":"alive"}`，不需要模型 |
| GET | `/ready` | 200/503 `{"ready", "loaded_release_id", "reason"}` |
| POST | `/v1/recommend` | 请求 `{"user_id", "top_k"}`，响应见 `examples/recommend-response.example.json` |

`/v1/recommend` 规则：
- `user_id` 去空白后非空、≤ 128 字符；`top_k` 1..`max_top_k`（`runtime.yaml`，≤ 50）。
- 先算 `user_key`，再查映射与已见集合；原始 user_id 不进日志。
- 已知用户：模型打分；未知用户：`pop_fallback`，`fallback_used=true`。
  - v1.2 补充（2026-11-23 用户批准）：champion 为 Pop 时，"已知用户" = `seen_items` 中有记录（`train_end` 前有任意评论）的用户；其余用户走同一个 `rank()` 的回退路径，`fallback_used=true`。推荐结果不变，只让监控的 fallback 率反映冷用户比例。离线评估口径不变（有效用户都有历史）。
- 已见商品排除、不重复、排名从 1 连续、分数有限。分数是排序分，不是概率。
- 没有可推荐商品：200，`items=[]`，`empty_reason="NO_CANDIDATES"`。
- `fusion_used` 在 P0 恒为 `false`。

## API-03 错误
| HTTP | code | 场景 |
|---|---|---|
| 422 | `INVALID_ARGUMENT` | 参数错误、JSON 解析失败（自定义 handler，统一 `ApiError` 格式） |
| 503 | `MODEL_NOT_READY` | 没有加载 release |
| 500 | `INTERNAL_ERROR` | 其他 |

## MON-01 事件
每个 `/v1/recommend` 请求（含 4xx/5xx）写一条事件到 `state/events.db`（SQLite），字段见 `schemas/monitoring-event.v1.schema.json`：
- `user_key_hash` = 同一把密钥的 HMAC；参数错误时为 null，不写请求体。
- `source`：正常请求 `api`；演示脚本发的请求带请求头 `X-RP-Source: synthetic_demo`，事件记 `synthetic_demo`。
- 写事件失败不影响推荐返回，只在进程内计数并打一行 warning。

## MON-02 汇总 `reviewpulse monitor summary [--since ...]`
按 `loaded_release_id` 和 `source` 分组输出：请求数、4xx/5xx 数、p50/p95 延迟、fallback 率、空结果率。事件少于 `min_events`（默认 20）时该组显示 `INSUFFICIENT_DATA`。
**线上推荐质量显示 `UNAVAILABLE`**：没有真实曝光/点击，不能拿离线 NDCG 冒充。

## 验收
- `API-T01` import `serving.app` 不触发加载（mock 掉 loader，断言未被调用）
- `API-T02` 无 champion：`/health` 200、`/ready` 503、`/v1/recommend` 503
- `API-T03` 合成 fixture 跑出的真实 release 能 ready 并返回推荐
- `API-T04` 晋升新 release 但不重启，`/ready` 仍报旧 `loaded_release_id`
- `API-T05` 空白 user_id、top_k=0、top_k=51、坏 JSON → 422 `ApiError`
- `API-T06` u1 的推荐不含 a、b（Golden Case）
- `API-T07` 未知用户 `fallback_used=true`
- `MON-T01` 200 / 422 / 503 各一次请求 → 恰好 3 条事件
- `MON-T02` 事件库里搜不到原始 user_id
- `MON-T03` 事件的 `loaded_release_id` 与 `/ready` 一致
- `MON-T04` 事件写入失败时推荐仍返回 200

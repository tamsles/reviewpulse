# v1.2 变更记录（2026-11-21）：5 天精简版

目的：把 v1.1 的 30 个任务压缩到一个人 5 天能做完、面试能讲清楚的范围。决策详见 ADR-0009（2026-11-21 用户审核：D1–D7 全部 ACCEPTED）。

判断标准：
- **保留**：面试会被问到、能体现 ML 工程水平、而且实现成本低的设计。
- **简化**：思想保留，实现降一档。
- **延后**：只在生产多实例 / 长期运营才有意义，或者属于 P1/P2 扩展。

## 保留（与 v1.1 一致）

| 项目 | v1.1 位置 | 为什么保留 |
|---|---|---|
| 全局时间切分 `train_end < valid_end < test_end` | TE-01 | 推荐系统最常见的错误就是随机切分导致未来泄漏，面试必问 |
| 已见 / 可推荐 / 真值 / Recall 分母口径 | ADR-0007 第 1–7 条 | 不定义清楚就没法写 Golden 测试，指标也不可信 |
| 剔除计数随指标报告 | TE-02 | 防止"好看但被筛过"的分数 |
| BPR 输给 Pop 时如实报告、可晋升 Pop | PRD-04、GATE-02 | 诚实对比是最大的加分点 |
| 门禁读登记的评估文件，不接受手填分数 | GATE-01、INV-06 | 一句话就能讲清楚，实现很便宜 |
| SQLite 单行表 + `version_counter` CAS | PROM-02 | 实现只是一条 `UPDATE ... WHERE version_counter = ?`，比 `os.replace` 更正确，而且好讲 |
| 无 Champion 可启动但 `/ready` 503 | API-01 | 旧项目 import 时加载模型的缺陷修复，好讲 |
| `/ready` 报告实际加载的 release ID | INV-08 | 一个字段，成本几乎为零 |
| 未知用户 Pop 冷启动 + `fallback_used` | REC-04 | 推荐系统基本功 |
| 测试集只在冻结后评估一次 | INV-03 | 基本科研规范 |
| 日志不存原始 user_id、评论原文 | INV-09 | 基本隐私规范 |
| 合成数据 `examples/synthetic_reviews.jsonl` 与示例响应 | examples | 已手算核对，直接作为测试 fixture |

## 简化

| 项目 | v1.1 | v1.2 | 理由 |
|---|---|---|---|
| BPR 实现 | RecBole adapter，必须证明读的是自定义切分 | **自写 PyTorch BPR（约 100 行）为默认**；RecBole 不做 | RecBole 与新版 PyTorch/NumPy 的兼容不确定，可能卡一整天；自写 BPR 损失面试更好讲；评估本来就用自有评估器 |
| 用户键 | 两把密钥 `K_map`（发布包）+ `K_tel`（遥测），必须不同 | **一把密钥 `RP_USER_KEY`**，发布包与事件都用 `HMAC-SHA256(key, user_id)`；Manifest 仍记 16 位 `user_key_id` 指纹 | 数据集的 user_id 本身已匿名化；双密钥的价值在多团队生产环境 |
| Release Manifest | 9 组字段、`schema_version 1.1`，融合字段等 | `schema_version 1.2`，去掉融合、`artifact_uri` 来源校验、`inference_config_sha256` | 只保留能防"换了映射/模型却没发现"的哈希 |
| 评估 Manifest | `purpose`、`evaluated_release_id`、`unscorable_item_count`、`metric_meta` 等 | 只保留切分 / 协议 / 真值哈希、指标与计数 | Champion 重评估已延后 |
| 门禁 | GATE-02/03/04，含性能预算、Champion 重评估 | 有限值 + 同协议 + 最低阈值 + 非 Pop 必须超过 Pop + 若有 Champion 则同切分比较，切分不同直接拒绝 `SPLIT_MISMATCH` | 没有反馈重训，就不会出现切分变化的常规场景 |
| 晋升状态机 | 7 个状态 + DEPLOY_FAILED + ROLLED_BACK | `DRY_RUN / REJECTED / CONFLICT / PROMOTED / ROLLED_BACK` | 部署核验已延后 |
| 部署 | desired/actual 分离、`deploy verify`、`deployments` 表 | 晋升只改指针；**重启服务**才生效；用 `/ready` 的 `loaded_release_id` 人工确认 | 单机单进程场景下这一层几乎只增加代码量 |
| 回滚 | 独立 CAS + 部署核验 | 同一个 CAS 函数，把指针改回历史上晋升过的 release | 复用晋升代码 |
| 监控 | 多进程安全、损坏事件隔离、7 项验收 | SQLite 事件表 + `monitor summary`（请求数、4xx/5xx、p50/p95、fallback 率） | 单 worker 足够 |
| CI | lint、静态检查、单元、E2E、Docker smoke、多平台 | `ruff` + `pytest`（合成 fixture，离线）+ `docker build`，Ubuntu 一个平台 | 5 天内够用 |
| Agent 流程 | 每个任务"计划 → 批准 → 实现 → 验收" | **按天批准**：每天开始给当天计划，结束给证据 | 30 轮审批不现实 |
| 仓库 | 旧 mini-mlops 仓库新分支演进 | 新建 `reviewpulse` 仓库，移植旧项目 `lineage.py` / `tracking.py` 的思路 | 面试官看仓库时历史干净；旧项目在 README 里作为"前身"提一句 |

## 延后（v1.1 中保留设计，v1.2 不实现）

| 项目 | v1.1 位置 | 面试时怎么说 |
|---|---|---|
| 情感特征融合重排序、E0–E3 消融 | 05、07、ADR-0007 第 8 条 | "as-of 特征截止口径已设计，下一步做" |
| 时序预测 | 06、ADR-0008 | 不提或一句带过 |
| Drift、反馈重训状态机 | 12 | "旧项目做过 KS 漂移检测，新项目里列为 future work" |
| Champion 重评估 GATE-04 | 09 | "反馈重训会换切分，届时需要在新切分上重算 Champion" |
| desired/actual 部署核验 | PROM-05、ADR-0005 | "多实例时需要，单机演示用 /ready 确认" |
| 双密钥 | ADR-0006 | "生产环境应该把发布包和遥测的密钥分开" |
| Dashboard | 14 | MLflow UI + 报告 Markdown 代替 |
| LightGCN、DistilBERT、SASRec | 04、05 | 可选 |

## 接口变化（相对 v1.1 草案）

- CLI：去掉 `deploy verify`、`drift`、`feedback`；其余见 `specs/06-ci-and-delivery.md`。
- `release-manifest.v1.2.schema.json` 取代 v1.1 版本；`deployment-record` schema 删除。
- `evaluation-manifest.v1.2.schema.json` 取代 v1.1 版本。
- OpenAPI、请求 schema、规范化交互 schema、Champion 状态 schema 不变（直接复制）；监控事件 schema 的 `source` 去掉 `offline_replay`。
- `/ready` 的 `reason` 枚举不变；`fusion_used` 字段保留且 P0 恒为 `false`（为以后的融合留位置，不改接口）。

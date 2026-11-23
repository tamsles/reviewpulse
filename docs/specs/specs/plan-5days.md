# 5 天计划

日期：2026-11-22（日）至 2026-11-26（四），日本时间。每天约 8 小时。时间是估计，Day 1 结束后按实际情况调整。每天按 `AGENTS.md` 的流程开始和汇报。

## Day 1（11/22 日）— 仓库、数据、切分
| Task | 内容 | 规格 | 估时 |
|---|---|---|---|
| T00 | 新建 `reviewpulse` 仓库、`pyproject.toml`、ruff、pytest、目录骨架、合成 fixture 放进 `tests/fixtures/` | 06 | 1h |
| T01 | 数据 Spike：下载一个小品类，核对字段与 timestamp 单位，统计规模和按月交互分布，定下 `source.yaml`、`split.yaml` 的边界；结果写 `reports/data-spike.md` | 01 | 2h |
| T02 | `data/contracts.py` + `prepare.py` + manifest | 01 | 2.5h |
| T03 | `data/split.py` + split manifest + 反例测试 | 01 | 2.5h |

**出口**：真实数据 prepare 两次哈希一致；DATA-T01..04、TE-T01、TE-T03 通过。

## Day 2（11/23 一）— 评估器与模型
| Task | 内容 | 规格 | 估时 |
|---|---|---|---|
| T04 | 统一 `rank()` + 评估器 + Golden Case 测试 | 02、03 | 2.5h |
| T05 | Pop | 03 | 0.5h |
| T06 | BPR（PyTorch）+ 早停 | 03 | 3h |
| T07 | MLflow 封装 + `train` CLI；真实数据上跑 Pop 和 BPR，在 `reports/progress.md` 记 validation 结果 | 03 | 2h |

**出口**：TE-T07/T08、EVAL-T01/T02、REC-T01..06 通过；真实数据上有 Pop vs BPR 的 validation 表（BPR 更差也照实记）。根据数字填好 `quality.yaml` 的阈值并写理由。

## Day 3（11/24 二）— Release、门禁、晋升
| Task | 内容 | 规格 | 估时 |
|---|---|---|---|
| T08 | `release/package.py` + `loader.py`（哈希、HMAC 键、smoke） | 04 | 3h |
| T09 | `registry/gate.py` | 04 | 2h |
| T10 | `registry/champion.py`：CAS 晋升、回滚、`champion show` | 04 | 3h |

**出口**：REL-T01..05、GATE-T01..04、PROM-T01..05 通过；真实数据上晋升了第一个 Champion（BPR 或 Pop）。

## Day 4（11/25 三）— API、监控、CI、Docker
| Task | 内容 | 规格 | 估时 |
|---|---|---|---|
| T11 | FastAPI lifespan、`/health`、`/ready`、`/v1/recommend`、错误处理 | 05 | 3h |
| T12 | 事件写入 + `monitor summary` | 05 | 2h |
| T13a | E2E 测试 + GitHub Actions | 06 | 1.5h |
| T13b | Dockerfile | 06 | 1.5h |

**出口**：API-T01..07、MON-T01..04、OPS-T02/T03 通过；CI 绿；真实 release 在本地 API 能推荐，延迟 p95 记入 progress。

## Day 5（11/26 四）— 最终评估与交付
| Task | 内容 | 规格 | 估时 |
|---|---|---|---|
| T14 | 冻结配置，`evaluate-test` 对 Pop 和选定的 BPR **各跑一次** | 02、06 | 1h |
| T15 | `reports/experiment.md` | 06 | 1.5h |
| T16 | README（含日本語の概要）+ `scripts/demo.sh` | 06 | 2.5h |
| T17 | （加分）情感分类 | 07 | 3h |

**出口**：OPS-T01、OPS-T04 通过；按 README 在干净目录里跑一遍快速开始。

## 落后时的砍单顺序（从先砍到后砍）
1. T17 情感分类
2. BPR 超参网格 → 只跑默认一组
3. Docker（T13b）→ README 写"本地运行"
4. `monitor summary` 的分组 → 只输出全局汇总
5. 回滚 CLI → 保留函数和测试，CLI 只留 promote

**不能砍**：Golden Case 测试、时间切分反例测试、CAS 测试、`/ready` 无 champion 行为、README 快速开始。

## 每日结束检查
- 当天测试全绿，已 commit
- `reports/progress.md` 更新了：做了什么、真实数字、没做完什么
- 明天第一件事写清楚

# ReviewPulse — 5 天精简版规格（v1.2）

> 编制日期：2026-11-21。基于 v1.1（`../reviewpulse-specs-v1.1/`）裁剪。
> **状态：设计，尚未实现。** 本包是给 Coding Agent 按天实现、给人审核的规格，不是可运行代码。
> 范围决策见 [`specs/decisions/ADR-0009-v1.2-five-day-scope.md`](specs/decisions/ADR-0009-v1.2-five-day-scope.md)（**ACCEPTED**，2026-11-21 用户批准 D1–D7）。

## 一句话

用 Amazon Reviews'23 一个小品类，做一个**能讲清楚的推荐 MLOps 闭环**：

```text
数据契约 → 时间切分 → Pop / BPR 训练 → 统一离线评估 → MLflow 记录
→ 打包 Release → 质量门禁 → 晋升 / 回滚 → FastAPI 推荐 → 请求日志 → 汇总报告
```

5 天内做完 P0，第 5 天有余力再做情感分类（离线，不进服务）。

## 为什么从 v1.1 裁剪

v1.1 有 30 个任务、7 个阶段，按生产系统标准设计。5 天、一个人、目标是日本 Web 公司新卒求职作品，需要的是：
1. **能跑**：fresh clone + 合成数据，几条命令跑通全链路；
2. **能讲**：每个设计都能在面试里用 1–2 句话解释为什么；
3. **诚实**：BPR 输给 Pop 也如实报告，不夸大离线指标。

v1.1 的完整设计**不删除**，作为"未来工作"保留在 `../reviewpulse-specs-v1.1/`，面试时可以说"完整设计在这里，我实现了其中 P0，其余列为 future work，原因是……"。

## 范围

| 类别 | 内容 |
|---|---|
| **必做（Day 1–4）** | 数据契约、时间切分、Pop、BPR、统一评估器 + 手算 Golden、MLflow、Release 包、门禁、SQLite CAS 晋升与回滚、FastAPI（/health、/ready、/v1/recommend）、请求事件 + 汇总、Docker、GitHub Actions CI |
| **必做（Day 5）** | 一次性最终测试评估、实验报告、README（含日文概要）、演示脚本 |
| **加分（Day 5 有余力）** | TF-IDF + LR 弱标签情感分类（离线评估，不进 Release） |
| **延后（不做）** | 情感融合重排序、时序预测、Drift、反馈重训、Dashboard、LightGCN、DistilBERT、部署核验记录（desired/actual 表）、双密钥、Champion 重评估（GATE-04） |

完整的保留 / 简化 / 延后对照见 [`CHANGES-v1.2.md`](CHANGES-v1.2.md)。

## 阅读顺序

1. [`AGENTS.md`](AGENTS.md)：Coding Agent 规则（比 v1.1 轻）
2. [`specs/constitution.md`](specs/constitution.md)：不可违反的规则（12 条）
3. [`specs/plan-5days.md`](specs/plan-5days.md)：**按天的任务、出口标准、落后时砍什么**
4. `specs/01` ~ `specs/07`：各模块规格
5. [`specs/acceptance.md`](specs/acceptance.md)：测试清单与任务映射
6. [`PROMPTS.md`](PROMPTS.md)：给 Claude Code 的提示词合集（初始化、每天开工/收工、续接、卡住时）
7. [`specs/backlog.md`](specs/backlog.md)：Day 5 之后剩下的待办（情感分类、演示 GIF、v1.1 文档、镜像瘦身、许可状态）

## 规格权威优先级（冲突时）

`constitution.md` > `ADR-0009` > `specs/01..07` > `plan-5days.md` > v1.1 文档 > Coding Agent 猜测。

v1.2 没写到的细节，**可以参考 v1.1 对应章节**，但只取与 v1.2 范围一致的部分；v1.1 中被 v1.2 明确简化或延后的要求不适用。

## 目标仓库结构

```text
reviewpulse/
  README.md            # 中文 + 日本語の概要
  AGENTS.md
  pyproject.toml       # 依赖锁定（uv / pip-tools 二选一）
  configs/             # source.yaml split.yaml train.yaml quality.yaml runtime.yaml
  src/reviewpulse/
    data/              # contracts.py prepare.py split.py
    evaluation/        # evaluator.py
    models/            # pop.py bpr.py
    tracking.py        # MLflow 封装
    release/           # package.py loader.py
    registry/          # gate.py champion.py
    serving/           # app.py
    monitoring/        # events.py summary.py
    cli.py
  tests/
    fixtures/synthetic_reviews.jsonl
  scripts/demo.sh
  reports/             # 实验报告（数字来自真实运行）
  Dockerfile
  .github/workflows/ci.yml
```

`data/`、`mlruns/`、`state/`、`releases/`、`logs/` 不进 Git。

## 外部材料

- Amazon Reviews'23：https://amazon-reviews-2023.github.io/index.html
- BPR 原论文：Rendle et al., "BPR: Bayesian Personalized Ranking from Implicit Feedback", UAI 2009

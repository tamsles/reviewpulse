# 给 Claude Code 的提示词合集（ReviewPulse v1.2）

> 用法：每个代码块就是一条完整的提示词，整块复制粘贴给 Claude Code（下文简称 CC）。
> 尖括号 `<...>` 的地方换成你的实际内容。
> 顺序：先用 §0 做一次准备 → 每天用 §1 开工 → 当天专用的 §2 → 收工用 §3。其余按需使用。

---

## 0. 开工前（只做一次）

### 0.1 先手动做的两件事

1. **仓库别放在桌面上。** 桌面上有一个 IWPU 项目的 `CLAUDE.md`（`C:\Users\Tamsles\Desktop\CLAUDE.md`）。CC 会自动读取上层目录的 `CLAUDE.md`，仓库放在桌面下就会把 IWPU 的规则混进来。建议放在：
   `C:\Users\Tamsles\Documents\reviewpulse`
2. 在 Claude Code 桌面版里新建会话，工作目录选上面这个文件夹（文件夹不存在就先建一个空的）。

### 0.2 初始化提示词（在新仓库的第一个会话里发）

```text
这是一个新项目 ReviewPulse（推荐系统 MLOps 求职作品，5 天完成）。我是机器学习和编程的初学者，请用中文、白话、分步骤和我沟通，每次说清楚"做了什么、为什么、下一步是什么"。

规格在 C:\Users\Tamsles\Desktop\reviewpulse-specs-v1.2（已定稿，ADR-0009 已批准）。旧版 v1.1 在 C:\Users\Tamsles\Desktop\reviewpulse-specs-v1.1，只作"未来工作"参考，不要实现 v1.1 里被 v1.2 简化或延后的东西。

这一轮只做初始化，不写业务代码：
1. 确认当前目录是 C:\Users\Tamsles\Documents\reviewpulse 且为空（不空就先列出内容问我）。
2. git init，默认分支 main。
3. 把规格整个复制到仓库的 docs/specs/ 下（保留目录结构；不要复制到仓库根目录，避免和项目 README 冲突）。
4. 在仓库根目录建 CLAUDE.md，内容只写：
   - 一句话项目说明
   - "每次会话先读 @docs/specs/AGENTS.md 和 @docs/specs/specs/plan-5days.md"
   - 沟通方式：中文、白话、初学者友好
   - 新写的 Python 文件保持 ASCII，注释用英文
   - 本仓库与桌面上的 IWPU / SCAR 项目无关
5. 建 .gitignore：data/ mlruns/ mlflow.db state/ releases/ logs/ .venv/ __pycache__/ *.parquet .env
6. 提出 Python 环境方案（Python 3.11；在 conda 新环境和 uv venv 里选一个推荐给我，说明理由），先不要安装，等我确认。

做完按 AGENTS.md 的汇报模板告诉我结果，然后等我说"开始 Day 1"。
```

---

## 1. 每天开工（通用）

```text
开始 Day <N>（<日期>）。

请先：
1. 读 CLAUDE.md、docs/specs/AGENTS.md、docs/specs/specs/constitution.md、docs/specs/specs/plan-5days.md 里 Day <N> 的部分，以及当天任务涉及的 docs/specs/specs/0x-*.md。
2. 读 reports/progress.md（如果有），确认昨天停在哪里、有什么没做完。
3. 输出 git status、当前分支、HEAD。
4. 给出今天的计划：任务顺序、每个任务要改哪些文件、要写哪些测试（写测试 ID）、用什么命令验证。

计划给我看，我说"开始"再动手。每完成一个任务：跑测试 → commit（message 带任务 ID）→ 用两三句白话告诉我这个任务做了什么。
```

---

## 2. 每天专用提示词（在 §1 之后、我说"开始"时一起发）

### Day 1（11/22）仓库、数据、切分：T00–T03

```text
开始。今天做 T00–T03，规格看 docs/specs/specs/01-data-and-split.md 和 06-ci-and-delivery.md。

T00：建 pyproject.toml（依赖先只放 pandas、pyarrow、pyyaml、pytest、ruff）、src/reviewpulse/ 目录骨架、tests/、configs/。把 docs/specs/examples/synthetic_reviews.jsonl 复制到 tests/fixtures/。跑通一个空测试。

T01 数据 Spike（下载前必须先问我）：
- 去 Amazon Reviews'23 官网（https://amazon-reviews-2023.github.io/）找小品类的评论文件下载地址，候选 All_Beauty 或其他评论数在几十万量级的品类。
- 下载前告诉我：文件名、来源 URL、大小，等我同意。
- 下载到 data/raw/（不进 Git）。读前 1000 行，核对字段名、timestamp 单位（看数值位数判断是 ms 还是 s，并换算几条成日期给我看）、user_id 和 parent_asin 的格式。
- 统计：总评论数、用户数、商品数、评分分布、按月的评论数。
- 提出 train_end / valid_end / test_end（让 valid 和 test 各约 10% 交互），说明理由。
- 写进 reports/data-spike.md，然后生成 configs/source.yaml 和 configs/split.yaml（参考 docs/specs/examples/ 下的同名示例）。边界给我确认后再继续。

T02：data/contracts.py + data/prepare.py + 数据 manifest。测试 DATA-T01..04。
T03：data/split.py + split manifest。测试 TE-T01、TE-T03。

今天的出口：真实数据 prepare 两次哈希一致；上面 6 个测试全过。
```

### Day 2（11/23）评估器与模型：T04–T07

```text
开始。今天做 T04–T07，规格看 docs/specs/specs/02-evaluation.md 和 03-models.md。

T04 最重要，先做：
- 写统一的 rank() 和 evaluate()。离线评估和以后的 API 必须共用 rank()。
- 先把 02-evaluation.md 里 EVAL-04 的 Golden Case 写成测试（TE-T07），再写实现让它通过。不许为了让测试通过而改 Golden Case 的数字或口径。
- 测试：TE-T07、TE-T08、EVAL-T01、EVAL-T02、REC-T04、REC-T05。

T05 Pop：REC-T01。
T06 BPR：用 PyTorch 自己写（ADR-0009 D2），不用 RecBole。写完后用白话给我讲一遍 BPR 的损失函数在代码里对应哪几行。测试 REC-T02、REC-T03、REC-T06。
T07 MLflow：tracking.py + `reviewpulse train --model Pop|BPR`。在真实数据上跑 Pop 和 BPR，超参只看 validation，网格最多 8 组；时间不够就只跑默认一组并说明。

今天的出口：真实数据上的 Pop vs BPR validation 表写进 reports/progress.md（附 run_id、split_sha256、所有剔除计数）。BPR 更差也照实写。然后根据真实数字提议 configs/quality.yaml 的三个阈值并写理由，等我确认。
```

### Day 3（11/24）Release、门禁、晋升：T08–T10

```text
开始。今天做 T08–T10，规格看 docs/specs/specs/04-release-gate-promotion.md 和 docs/specs/schemas/release-manifest.v1.2.schema.json。

T08：release/package.py + release/loader.py。用户键用 HMAC-SHA256(RP_USER_KEY, user_id)，密钥只从环境变量读，绝不写进代码、配置、manifest 或日志。测试 REL-T01..05。
T09：registry/gate.py，按 GATE-01 表格的 6 条顺序检查。门禁函数只接受 release_id，不接受任何分数参数。测试 GATE-T01..04。
T10：registry/champion.py。SQLite 单行表，version_counter 做 CAS；promote / rollback 默认 dry-run。测试 PROM-T01..05，其中 PROM-T03 要真的用两个线程或进程并发。

做完 T10 后用白话给我讲：CAS 为什么用版本号而不是 release_id（A→B→A 的例子）。

今天的出口：上面 14 个测试全过；在真实数据上打包 Pop 和 BPR 两个 release，跑门禁，晋升第一个 champion。把门禁结果和 champion show 的输出贴进 reports/progress.md。
```

### Day 4（11/25）API、监控、CI、Docker：T11–T13b

```text
开始。今天做 T11、T12、T13a、T13b，规格看 docs/specs/specs/05-serving-and-monitoring.md、06-ci-and-delivery.md 和 docs/specs/schemas/openapi.v1.yaml。

T11：serving/app.py。模型只在 lifespan 里加载，import 时不加载；无 champion 时 /ready 和 /v1/recommend 返回 503；推荐必须调用 Day 2 写的同一个 rank()。参数错误统一 422 + ApiError。测试 API-T01..07。
T12：monitoring/events.py + summary.py，SQLite 事件表，不存原始 user_id。测试 MON-T01..04。
T13a：一个 E2E 测试（合成 fixture：prepare → split → train → package → promote → TestClient 请求）+ .github/workflows/ci.yml（ubuntu、Python 3.11、ruff、pytest，全程离线）。OPS-T03。
T13b：Dockerfile，releases/ 和 state/ 用卷挂载，密钥用环境变量。OPS-T02。如果我电脑上没有 Docker，就只写 Dockerfile 并在 CI 里 docker build，告诉我哪些没在本地验证。

今天的出口：CI 绿（推到 GitHub 之前先问我仓库名和是否公开）；本地用真实 release 启动 API，用 curl 演示一次已知用户、一次未知用户、一次参数错误，记下 p95 延迟。
```

### Day 5（11/26）最终评估与交付：T14–T17

```text
开始。今天做 T14–T16，T17 是加分项，前面都完成再做。规格看 docs/specs/specs/06-ci-and-delivery.md 和 07-stretch-sentiment.md。

T14：先列出要冻结的配置和要评估的两个 run（Pop 和选定的 BPR），给我确认后，用 `reviewpulse evaluate-test --confirm-final` 各跑一次。test 只能跑这一次；跑完不许再根据 test 结果改任何配置。
T15：reports/experiment.md。数字全部来自真实运行，附 run_id、split_sha256；validation 表和 test 表格式相同；写清所有剔除计数、局限性，以及"评分评论 ≠ 购买，离线 NDCG ≠ 线上 CTR"。
T16：README.md（中文为主，加一段 10 行左右的"日本語の概要"）+ scripts/demo.sh。README 要包含：架构图（Mermaid）、5 分钟合成数据快速开始、真实数据复现、结果表、"设计上的取舍"、"生产化还需要什么"（指向 docs/specs 里的 v1.1 设计）。最后在一个干净的临时目录里 clone 一份，按 README 快速开始跑一遍，证明能跑通。
T17（可选）：情感分类，规格 07。

今天的出口：OPS-T01、OPS-T04 通过；README 快速开始在干净目录跑通。
```

---

## 3. 每天收工

```text
今天收工。请：
1. 跑一遍全部测试，贴出结果和 exit code。
2. 确认所有改动已 commit，贴 git log --oneline 今天的部分。
3. 按 AGENTS.md 的汇报模板汇报。
4. 更新 reports/progress.md：今天完成的任务、真实数据上的数字、没做完的、明天第一件事。
5. 对照 plan-5days.md 判断进度：正常 / 落后（落后多少，建议按砍单顺序砍什么）。先建议，不要直接砍。
```

---

## 4. 按需使用

### 4.1 新开会话、续接（上下文丢了或换了会话）

```text
这是续接会话，不要凭记忆。请先读 CLAUDE.md、docs/specs/AGENTS.md、docs/specs/specs/plan-5days.md、reports/progress.md，再看 git log --oneline -20 和 git status，然后告诉我：现在是 Day 几、哪些任务已完成（以测试和 commit 为证据）、下一步是什么。等我确认再继续。
```

### 4.2 进度落后

```text
今天进度落后了。请按 plan-5days.md 的"砍单顺序"给出方案：砍掉什么、省下多少时间、对最终演示和 README 有什么影响。"不能砍"清单里的东西不许动。给我看方案，我同意再改。改完后在 reports/progress.md 记下砍了什么、为什么。
```

### 4.3 卡住 / 报错

```text
遇到问题：<贴报错或描述>。
请按这个顺序处理：
1. 用白话说明报错是什么意思、最可能的原因（列 2–3 个）。
2. 先用最小的命令确认原因，不要一上来就改代码。
3. 给出修复方案和影响范围，涉及改评估口径、门禁规则、测试预期的，先问我。
4. 修完跑相关测试，告诉我结果。
如果同一个问题试了 3 次还没解决，停下来把已知信息整理给我，不要继续乱试。
```

### 4.4 commit 前自查

```text
在 commit 之前请自查当前改动：
- 有没有违反 constitution.md 的 12 条（特别是：用了 train_end 之后的数据、日志或文件里出现原始 user_id、门禁接受手填分数）？
- 对应的测试 ID 是否都有，正常路径和失败路径各至少一个？
- 有没有顺手加了 v1.2 范围外的功能？
- 有没有把密钥、数据文件、mlruns、state 加进 Git？
逐条回答，有问题先修。
```

### 4.5 帮我理解代码（每天收工后或面试前）

```text
我是初学者，请带我读今天写的代码：
1. 按数据流的顺序，列出今天新增的文件和关键函数，每个用一两句白话说明。
2. 挑最重要的一个函数，逐段解释它在做什么、为什么这样写。
3. 出 3 个面试官可能会问的问题，附上参考回答（用我能说出口的话，不要太书面）。
```

### 4.6 面试准备（Day 5 之后）

```text
根据 README、reports/experiment.md 和代码，帮我准备面试：
1. 30 秒和 2 分钟两个版本的项目介绍（中文各一份，再各给一份日语）。
2. 10 个可能的技术追问和参考回答，重点放在：时间切分、评估口径、BPR、为什么 Pop 能晋升、CAS、HMAC、为什么不报线上 CTR。
3. 3 个"如果重新做 / 生产化你会怎么改"的回答（引用 docs/specs 里的 v1.1 设计）。
写进 reports/interview-prep.md（这个文件不要 commit，加进 .gitignore）。
```

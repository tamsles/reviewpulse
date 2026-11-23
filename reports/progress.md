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
Day 2 的 T04：`rank()` + 评估器 + Golden Case 测试（TE-T07/T08、EVAL-T01/T02）。
（更正：Day 2 也在 11/23 当天完成，日程已经追回，不需要顺延。）

## Day 2（2026-11-23（一），与计划一致）

| Task | 状态 | 证据 |
|---|---|---|
| T04 rank() + 评估器 + Golden | 已实现 / 已测试 | TE-T07、TE-T08、EVAL-T01、EVAL-T02、REC-T04、REC-T05 通过 |
| T05 Pop | 已实现 / 已测试 | REC-T01 通过（用真实 Pop 跑的 Golden 端到端结果一致） |
| T06 BPR | 已实现 / 已测试 | REC-T02、REC-T03（新进程加载）、REC-T06 通过；负样本反例测试通过 |
| T07 MLflow + train CLI | 已实现 / 已测试；真实数据已跑 | params、metrics、artifacts 检查通过；C-09 扫描通过 |

### 验证命令
- `ruff check .`、`ruff format --check .` → exit 0
- `pytest` → 116 passed，exit 0
- `reviewpulse train --model Pop` → exit 0，11 秒
- `reviewpulse train --model BPR --grid` → exit 0，365 秒（8 组）
- 所有真实数据的 run 都在 commit `0338be2` 上跑，`git_dirty=false`

### 真实数据 validation 结果（All_Beauty）
`split_sha256 = 21a1d1c62badc5a2fa386f808e526273572108d14e621a28172a5ef2cf96def3`，所有 run 的三个哈希一致。

计数（所有 run 都相同）：有效用户 2,122；没有真值的 warm 用户 3,129；因已见剔除的真值 13；因冷商品剔除的真值 16,369；eligible 商品 88,939。BPR 回退到 Pop 的用户 377 人（train 里没有正向评价，所以不在 BPR 用户映射里）。

| run_id | 模型 | dim | lr | reg | best / 实际跑的 epoch | 训练秒 | NDCG@10 | Recall@10 | Coverage@10 |
|---|---|---|---|---|---|---|---|---|---|
| `1ac6483d…` | **Pop** | — | — | — | — | <1 | **0.00842** | **0.01579** | 0.00012 |
| `9c62b80a…` | BPR | 64 | 5e-3 | 1e-4 | 5 / 10 | 51 | 0.00458 | 0.00872 | 0.02637 |
| `a8c39510…` | BPR | 64 | 5e-3 | 1e-5 | 5 / 10 | 50 | 0.00458 | 0.00872 | 0.02616 |
| `10eb40cd…` | BPR | 32 | 5e-3 | 1e-5 | 5 / 10 | 42 | 0.00372 | 0.00754 | 0.02114 |
| `5f3a09b5…` | BPR | 32 | 5e-3 | 1e-4 | 5 / 10 | 42 | 0.00372 | 0.00754 | 0.02131 |
| `b7956693…` | BPR | 64 | 1e-3 | 1e-5 | 1 / 6 | 34 | 0.00296 | 0.00566 | 0.15109 |
| `21969b00…` | BPR | 64 | 1e-3 | 1e-4 | 1 / 6 | 34 | 0.00296 | 0.00566 | 0.15109 |
| `6ecbf9b8…` | BPR | 32 | 1e-3 | 1e-5 | 1 / 6 | 33 | 0.00296 | 0.00566 | 0.12428 |
| `9d81c4c8…` | BPR | 32 | 1e-3 | 1e-4 | 1 / 6 | 30 | 0.00296 | 0.00566 | 0.12430 |

**结论：BPR 不如 Pop**（按 C-12 照实写）。
- 选定的 BPR：`9c62b80a`（dim=64、lr=5e-3、reg=1e-4）。它和 `a8c39510` 的 NDCG、Recall 完全相同，按 Coverage 更高来打破并列。
- 配对差（BPR − Pop）= −0.00383，配对标准误 0.00129，bootstrap 95% 置信区间 [−0.00645, −0.00149]。区间不包含 0，所以 Pop 更好不是噪声造成的。
- 按用户拆开看：在 BPR 认识的 1,745 个用户上，BPR 是 0.00197，Pop 是 0.00663；回退的 377 个用户贡献了 0.00296，两边完全相同。
- **lr=1e-3 的 4 组没训练起来**：损失只从 0.694 降到 0.669，validation NDCG 完全不变，于是在第 6 个 epoch 被早停（patience 5）。0.00296 全部来自回退用户，这几组 BPR 本身等于随机。
- lr=5e-3 的 4 组在第 5 个 epoch 达到峰值，之后损失继续下降，validation 却下降，属于过拟合。
- 噪声参考：Pop NDCG 的标准误是 0.00153；随机排序的期望 NDCG（|rel|=1）约为 0.000051。

### 未完成 / 未验证
- `configs/quality.yaml` 已按用户确认的值写入：min_abs_ndcg=0.004、min_improvement=0.0025、max_regression=0.0（理由写在文件注释里）。
- lr=1e-3 时 patience 5 不够，模型还没进入有效训练就被停了。没有为此改规格（网格和 patience 都是 MODEL-03 定的），作为局限写进 experiment.md。

### 明天第一件事
Day 3 的 T08：`release/package.py` + `loader.py`。

## Day 3（2026-11-23（一），比计划提前一天）

| Task | 状态 | 证据 |
|---|---|---|
| T08 package + loader | 已实现 / 已测试；真实数据已打包 | REL-T01..05 通过 |
| T09 gate | 已实现 / 已测试；真实数据已跑 | GATE-T01..04 通过；C-05 签名测试通过 |
| T10 champion CAS | 已实现 / 已测试；真实数据已晋升 | PROM-T01..05 通过（PROM-T03 用两个真实线程，连续重复 5 次都通过） |

规格补充（用户批准，已写进 `04-release-gate-promotion.md`）：`pop_fallback.json` 存 `[item_id, score]` 列表；Pop 基线从 `releases/` 中三个哈希一致的 Pop release 里找；门禁决定写到 `state/gate/`。
实现上的扩展：`promotion_log` 多一列 `gate_decision_sha256`（回滚时恢复用）；dry-run 完全不写数据库；门禁跑完后 champion 如果已经变了，写入视为 `CONFLICT`；`release_id` 校验格式，防止路径穿越。

### 验证命令
- `ruff check .`、`ruff format --check .` → exit 0
- `pytest` → 172 passed，exit 0
- `reviewpulse package` ×3 → exit 0，每个约 25 秒
- `reviewpulse gate` ×3 → Pop exit 0，另外两个 exit 3（REJECTED）
- `reviewpulse promote --expected-version 0` → exit 0（DRY_RUN，没有生成 registry.db）
- `reviewpulse promote --expected-version 0 --execute` → exit 0（PROMOTED）
- 真实 release 扫描：24 个文件对照 631,986 个真实 user_id，命中 0

### 真实数据 release 和门禁结果
| release | 模型 | run | build | 门禁 | 原因 |
|---|---|---|---|---|---|
| `rel-1fd01c0aa3d4` | Pop | `0711a659…` | `741fa16`，clean | **PASSED** | 0.008416 ≥ 0.004；第 5、6 条跳过（Pop 本身就是基线，也还没有 champion） |
| `rel-afe1b95456a0` | BPR | `9c62b80a…` | `0338be2`，clean | REJECTED | `NOT_BETTER_THAN_POP`：0.004583 ≤ 0.008416 + 0.0025（基线是 `rel-1fd01c0aa3d4`） |
| `rel-fb924c045eab` | Pop | `1ac6483d…` | `0539759`，**dirty** | REJECTED | `DIRTY_BUILD` |

为什么会有 dirty 的 Pop：Day 2 跑 Pop 时，我把输出重定向到了 `reports/.pop_run.json`。shell 在训练开始前就创建了这个文件，于是这次 run 被如实记成 dirty。之后在干净的工作区上重训了 Pop（run `0711a659`），指标和计数逐位相同（NDCG 0.008416187638440997）。dirty 的那个 release 保留下来，作为门禁拒绝 dirty build 的真实例子。

release 大小：BPR 184MB，Pop 47MB（`seen_items.json` 含 50 万个 user_key）。

### `champion show --history 5`
```json
{
  "champion": {
    "actor": "local-cli",
    "gate_decision_sha256": "8a3d160623183f19b4edcf2914fafff5cd1475ad0f4f27dab11b1b60b6d9adaf",
    "previous_release_id": null,
    "promoted_at_utc": "2026-11-23T07:28:51Z",
    "release_id": "rel-1fd01c0aa3d4",
    "schema_version": "1",
    "version_counter": 1
  },
  "history": [
    {"action": "PROMOTE", "actor": "local-cli", "expected_version": 0, "id": 1, "reason": null,
     "release_id": "rel-1fd01c0aa3d4", "status": "PROMOTED", "time_utc": "2026-11-23T07:28:51Z"}
  ]
}
```

### 未完成 / 未验证
- 门禁找 Pop 基线时不检查基线本身是不是 dirty build。这次的基线是 clean 的那个，而且两个 Pop 的指标完全相同，所以没影响。
- 每次 CLI 启动都会 import torch，大约慢 1～2 秒，没有优化。

### 明天第一件事
Day 4 的 T11：FastAPI 的 lifespan、`/health`、`/ready`、`/v1/recommend`（用 `load_release` 加载 champion，推荐走同一个 `rank()`）。

## Day 4（2026-11-23（一），比计划提前两天）

| Task | 状态 | 证据 |
|---|---|---|
| T11 FastAPI | 已实现 / 已测试；真实 release 已在本地跑过 | API-T01..07 通过 |
| T12 事件 + monitor summary | 已实现 / 已测试；真实数据已跑 | MON-T01..04 通过 |
| T13a E2E + CI | 已实现 / 已测试；**CI 绿** | GitHub Actions run 45135936237（commit `fa53e21`）：`test` job（lint + pytest）success，`docker` job（build + OPS-T02）success |
| T13b Dockerfile | 已实现，**本地已验证 OPS-T02** | 本地 build 成功（231 秒，2.15GB，torch 2.15.0+cpu）；没有 champion 时 `/health` 200、`/ready` 503 `NO_CHAMPION` |

### 验证命令
- `ruff check .`、`ruff format --check .` → exit 0
- `pytest` → 216 passed，exit 0
- `docker build -t reviewpulse:local .` → exit 0
- `docker run`，挂空的 state 和 releases → `/health` 200，`/ready` 503，`/v1/recommend` 503 `MODEL_NOT_READY`
- `docker run`，挂真实的 state 和 releases（`--env-file .env`）→ 约 10 秒 ready，推荐返回 200
- `reviewpulse serve`（本地，真实 champion `rel-1fd01c0aa3d4`）→ 已知用户、未知用户、`top_k=0` 三种请求分别返回 200、200、422

### 真实数据的延迟（200 个请求，已知用户和未知用户各 100，top_k=10，本机 Windows，单 worker）
| 口径 | p50 | p95 |
|---|---|---|
| 服务端（`monitor summary`，事件里的 latency_ms） | 2.05 ms | **2.42 ms** |
| 客户端（httpx 顺序请求，含 HTTP 往返） | 7.05 ms | **7.87 ms** |

`monitor summary` 的统计范围包括 200 个计时请求、5 个预热请求和 3 个演示请求，共 208 条，其中 1 条是 422。fallback_rate = 0，原因见下面"待决定"。
隐私扫描：`state/events.db` 和 `logs/serve.log` 对照 631,986 个真实 user_id，命中 0。

### 已决定（规格不明确处，用户已批准，已写进 05 规格）
champion 是 Pop 时，`train_end` 前没有任何评论的用户返回 `fallback_used=true`：Pop 模型只"认识" `seen_items` 里的用户，其余用户走同一个 `rank()` 的回退路径。推荐结果不变，离线评估口径不变（`fallback_user_count` 仍为 0）。上面 200 个请求的延迟测试是在改动前跑的，所以当时的 fallback_rate 显示 0；按新规则，那批请求里一半是未知用户，fallback_rate 会是约 0.5。

### 未完成 / 未验证
- Docker 镜像以 uid 1000 运行。在 Linux 主机上挂载卷时，如果宿主目录属于别的 uid，写 `state/` 会失败（Windows 的 Docker Desktop 没有这个问题）。README 里会写明 `--user $(id -u)` 的用法。
- 镜像 2.15GB 偏大，主要是 torch 和 mlflow。serving 其实用不到 mlflow，可以拆成可选依赖，暂时没做。

### 明天第一件事
Day 5 的 T14：冻结配置，`evaluate-test` 对 Pop 和选定的 BPR 各跑**一次**。

## Day 5（2026-11-23（一），比计划提前三天）

| Task | 状态 | 证据 |
|---|---|---|
| T14 冻结 + test 评估 | 已完成；**每个 run 只跑了一次** | `reports/frozen_config.json`（`cdce0de`）；`reports/test_runs.log` 正好 2 条正式记录；重复运行返回 exit 1（OPS-T04） |
| T15 experiment.md | 已完成 | `reports/experiment.md` |
| T16 README + demo.sh | 已完成；在干净 clone 里验证过 | 从 GitHub clone 到临时目录，按 README 安装、pytest、demo.sh，全部 exit 0，合计约 3.6 分钟（OPS-T01） |
| T17 情感分类 | **没做**（用户决定先不做；也是砍单顺序里第一个） | — |

### test 结果（冻结后，每个模型只跑一次）
| 模型 | run_id | NDCG@10 | Recall@10 | Coverage@10 | 有效用户 | 回退用户 |
|---|---|---|---|---|---|---|
| Pop | `0711a659…` | 0.007975 | 0.020264 | 0.000124 | 1,135 | 0 |
| BPR | `9c62b80a…` | 0.002961 | 0.007930 | 0.020407 | 1,135 | 236 |

剔除计数（test）：已见 15，冷商品 26,223，没有真值的 warm 用户 2,942。两个模型的 `ground_truth_sha256` 相同（`aa7b3a03…`）。

### 其他改动
- 所有 JSON 和日志改为固定写 LF。这是在两次正式评估之后才发现并修复的，两次评估用的是同一份代码，指标不受影响。日志里记录的 `evaluation_sha256` 是 CRLF 版本的哈希，Git 里 LF 版本的哈希已写进 experiment.md 第 8 节。
- `git_info` 改为检查代码所在的仓库，而不是 `RP_HOME`。这样在临时目录里跑演示，也能正确记录 clean build。
- 用旧版本号晋升时，CONFLICT 的提示会直接写出两个版本号。

### 验证命令
- `ruff check .`、`ruff format --check .` → exit 0
- `pytest` → 232 passed，exit 0
- `reviewpulse evaluate-test --confirm-final` ×2 → exit 0；第三次（重复跑 Pop）→ exit 1
- `bash scripts/demo.sh`（本地、干净 clone 各一次）→ exit 0
- GitHub Actions：`63b99c9` 的 run 45138863512 → success

### 未完成 / 未验证
- T17 情感分类没有做。
- 没有录终端演示 GIF（可选项）。
- `license_review_status` 仍为 `pending`。

## Day 5 追加（2026-11-23）

### 修复：test 记录的哈希不一致
- 问题：`reports/test_runs.log` 里两条正式记录的 `evaluation_sha256`，是 Windows 上写出的 CRLF 文件的哈希；Git 里保存的是 LF 文件，clone 下来对不上。
- 处理：**原有记录一行都没改**。为每个 run 各追加一条 `formal: false` 的 `evaluation_sha256_correction` 记录（731a2e0c→235f8597，cf290de1→53c30a18）。追加前先验证了"LF 文件换回 CRLF 后，正好等于原哈希"，也就是内容完全相同。工作区里的 4 个报告文件已经重新检出为 LF。
- 新增：`final_eval.verify_test_records()` / `correct_evaluation_hash()`。前者检查正式记录和文件是否一致，后者在内容有任何其他改动时都会拒绝更正。`tests/test_reports_integrity.py` 在 CI 里检查真实的报告文件：哈希链一致、每个 run 正好一条正式记录、配置自冻结以来没变、报告文件都是 LF。修复前这个测试是失败的（能抓到这个问题），修复后通过。
- 配置文件不受影响：4 个 yaml 在冻结时就是 LF，冻结时记录的哈希和 Git 里的文件一致。

### 待办（用户决定先不处理）
- **v1.1 设计文档不在公开仓库里**：README 的"生产化还需要什么"目前链接的是 `docs/specs/CHANGES-v1.2.md` 和 ADR-0009。v1.1 原文在仓库外的 `../reviewpulse-specs-v1.1/`。以后要决定是否把它（或它的摘要）放进仓库，以及放之前是否需要脱敏。

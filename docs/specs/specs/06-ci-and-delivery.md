# 06 CLI、配置、CI、Docker 与交付物

## CLI（`reviewpulse`，用 Typer 或 argparse）
```bash
reviewpulse data prepare   --source configs/source.yaml          # 原始 → 规范化 + manifest
reviewpulse data split     --split configs/split.yaml            # 切分 + manifest
reviewpulse train --model Pop                                    # 训练 + 验证评估 + MLflow
reviewpulse train --model BPR [--grid]
reviewpulse package --run-id <id> [--allow-dirty]                # 生成 releases/<release_id>/
reviewpulse gate --release-id <id>                               # 只看门禁结果
reviewpulse promote  --release-id <id> --expected-version <n> [--execute]
reviewpulse rollback --release-id <id> --expected-version <n> [--execute]
reviewpulse champion show
reviewpulse serve                                                # uvicorn, 单 worker
reviewpulse monitor summary
reviewpulse evaluate-test --run-id <id> --confirm-final          # Day 5 只跑一次，写 reports/test_runs.log
```
所有路径以 `RP_HOME`（默认项目根）为基准，不依赖当前工作目录。

## 配置
| 文件 | 内容 |
|---|---|
| `configs/source.yaml` | 品类、字段映射、timestamp 单位、正向阈值（见 examples） |
| `configs/split.yaml` | 时间边界与评估口径（见 examples） |
| `configs/train.yaml` | seed、BPR 超参网格、早停 |
| `configs/quality.yaml` | 门禁阈值（见 examples） |
| `configs/runtime.yaml` | `max_top_k`、`min_events`、密钥环境变量名（`RP_USER_KEY`，不含值） |

## CI（`.github/workflows/ci.yml`，ubuntu-latest，Python 3.11）
1. `ruff check` + `ruff format --check`
2. `pytest`：全部用 `tests/fixtures/synthetic_reviews.jsonl`，离线，固定 seed，测试里设置一个假 `RP_USER_KEY`
3. 一个 E2E 测试：prepare → split → train Pop/BPR(2 epoch) → package → promote → 启动 app（TestClient）→ 推荐
4. `docker build`（不在 CI 里跑真实数据）

不下载数据集，不访问外网（除安装依赖）。

## Docker
- 镜像只含代码和依赖；`releases/`、`state/` 通过卷挂载；`RP_USER_KEY` 通过环境变量传入。
- 无 champion 时容器能启动且 `/ready` 503。

## 交付物（Day 5）
1. **`reports/experiment.md`**：数据规模表、切分边界、Pop vs BPR 的 validation 表 + test 表（同格式、同一切分）、所有剔除计数、BPR 超参选择过程、训练耗时、推理 p95、局限性。数字全部来自真实运行，附 run_id 与 split_sha256。
2. **`README.md`**：
   - 一段话说明项目 + 架构图（Mermaid）
   - 5 分钟合成数据快速开始（fresh clone 能跑）
   - 真实数据复现步骤
   - 结果表（链接 experiment.md）
   - "设计上的取舍"：为什么时间切分、为什么用版本号 CAS、为什么 Pop 可以晋升、为什么不报线上 CTR
   - "生产化还需要什么"（链接 v1.1 设计：部署核验、双密钥、Drift、反馈重训）
   - **日本語の概要**（10 行左右：目的、技術スタック、工夫した点、結果）
3. **`scripts/demo.sh`**：在合成数据上一条命令演示全流程（含一次 BPR 被拒 / Pop 晋升或回滚的场景），每步打印说明。
4. 可选：录一段 2–3 分钟的终端演示 GIF 放进 README。

## 验收
- `OPS-T01` fresh clone 后按 README 的快速开始命令能跑通（CI 的 E2E 即证据）
- `OPS-T02` Docker 无 champion 能启动、`/ready` 503
- `OPS-T03` 仓库里没有数据文件、密钥、`mlruns/`、`state/`
- `OPS-T04` `reports/test_runs.log` 中 test 评估只有一条正式记录

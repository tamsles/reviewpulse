# 待办清单（Day 5 之后）

> 记录日期：2026-11-23。Day 1–5 的必做任务已经全部完成（见 `reports/progress.md`）。下面是剩下的、用户决定**以后再做**的事。
> 每一项开始前都要先问用户；完成后在这里标记完成，并更新 `reports/progress.md`。

## 所有待办都要遵守的共同约束
- **test 评估已经冻结，不能再跑**：`reports/frozen_config.json` 和 `reports/test_runs.log` 不能修改（哈希更正只能追加，见 `final_eval.correct_evaluation_hash()`）。冻结的 4 个配置是 `source.yaml`、`split.yaml`、`train.yaml`、`quality.yaml`，同样不能改，`tests/test_reports_integrity.py` 会检查它们。
- 不为了某个待办去修改评估口径、门禁阈值，或者删测试。
- 新的 Python 文件保持 ASCII，注释用英文。

---

## B1 T17 情感分类（加分项）
- **内容**：按 [`07-stretch-sentiment.md`](07-stretch-sentiment.md) 实现 TF-IDF + LR 的弱标签情感分类，只做离线评估。
- **为什么**：证明同一套 MLflow 记录和时间切分，也能用在第二种任务上。这是砍单顺序里第一个被砍的。
- **约束**：
  - 只用 `train_end` 之前的评论（SENT-02），不碰推荐的 valid/test 窗口。
  - 不进 Release、不进 API、不做融合。
  - 新配置单独放一个文件（例如 `configs/sentiment.yaml`），不要改冻结的 4 个配置。
  - 报告里写清楚：星级代理标签不是人工标注的情感真值。
- **验收**：07 规格里的测试通过；`reports/experiment.md` 新增情感分类一节，数字来自真实运行；全量 pytest 和 CI 都是绿的。
- **估时**：约 3h。

## B2 终端演示 GIF（可选）
- **内容**：把 `bash scripts/demo.sh` 的运行过程录成 GIF 或 asciinema，放进 README 的快速开始附近。
- **为什么**：面试官不一定会自己跑，看一段录屏更直观。
- **约束**：只用合成数据；画面里不能出现真实的 user_id、评论原文或 `RP_USER_KEY`。文件尽量小（目标 < 5 MB）。
- **验收**：README 里能正常显示；人工检查一遍画面里没有敏感信息。
- **估时**：约 0.5–1h。

## B3 v1.1 设计文档要不要放进仓库
- **内容**：现在 v1.1 的完整设计放在仓库外的 `../reviewpulse-specs-v1.1/`，README 和 `docs/specs/README.md` 只是提到它。要决定：放全文、放摘要，还是不放。
- **为什么**：面试时说"完整设计在这里"，最好能给出链接。
- **需要用户决定**：
  - 方案 A：放一份摘要，比如 `docs/specs/v1.1-summary.md`，只列被延后的设计和延后的理由。工作量小，暴露的内容也少。
  - 方案 B：放全文。放之前要先检查一遍：有没有个人信息、内部路径、和本项目无关的内容，以及跟 v1.2 冲突、容易让人误解的地方。
- **验收**：README 的"生产化还需要什么"一节链接到放进来的文档；仓库里没有敏感信息。
- **估时**：A 约 1h，B 约 2h（含检查）。

## B4 缩小 Docker 镜像
- **内容**：现在的镜像有 2.15 GB，主要是 torch 和 mlflow 占的。serving 用不到 mlflow，可以把 mlflow 拆成可选依赖（例如 `[train]`），镜像里只装服务需要的依赖。
- **为什么**：镜像更小，build 和拉取都更快，也更像生产环境的做法。
- **约束**：`serving/` 和 `release/loader.py` 不能 import mlflow；本地开发和 CI 的安装方式（`uv pip install -e ".[dev]"`）照样能跑全部测试。
- **验收**：OPS-T02 在新镜像上通过（没有 champion 时 `/health` 200、`/ready` 503）；在 progress 里记录新的镜像大小；CI 是绿的。
- **估时**：约 1.5h。

## B5 数据许可状态的后续
- **现状**：2026-11-23 已经核对过：这份数据没有正式许可证，维护者表示主要供研究用途。记录在 `reports/data-spike.md`。因为 `source.yaml` 是冻结的配置，`license_review_status` 仍写 `pending`。
- **要做的**：现在不用做。以后如果开始新一轮评估（新的切分或新的品类，会重新冻结），在新的 `source.yaml` 里把这个值改成核对后的结论，例如 `reviewed-no-license-research-use`。
- **约束**：不能为了改这个字段去动当前的冻结。

---

## 已完成
（暂无）

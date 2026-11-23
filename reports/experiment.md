# ReviewPulse 实验报告

> 本报告的所有数字都来自真实运行（Amazon Reviews'23 `All_Beauty`，日期为 2026-11-23），每个结果都附 run_id 和 split_sha256。合成 fixture 只用于测试和 CI，不出现在本报告的结果表里。

## 1. 结论摘要
- **离线指标上 Pop 优于 BPR**。validation 上 Pop 的 NDCG@10 是 0.00842，BPR 是 0.00458；test 上 Pop 是 0.00797，BPR 是 0.00296。validation 上配对差的 95% bootstrap 置信区间是 [−0.00645, −0.00149]，不包含 0。
- 质量门禁让 **Pop 成为 champion**（`rel-1fd01c0aa3d4`）。BPR 被门禁以 `NOT_BETTER_THAN_POP` 拒绝。
- 主要原因是数据极度稀疏：92% 的用户只有 1 条评论，评估窗口里的正向评价大多落在 train 期间没出现过的新商品上。
- 这是一个**单机演示**项目，展示的是"可复现的离线评估 + 可审计的发布流程"，不代表线上推荐效果（见第 8 节）。

## 2. 数据
| 项目 | 值 |
|---|---|
| 来源 | Amazon Reviews'23，`All_Beauty`，评论文件 94,441,517 字节，sha256 `ee00e668…e349` |
| 许可审核 | `pending`（仓库只包含下载脚本和 manifest，不包含数据） |
| timestamp | 毫秒（Spike 时用真实记录核对过，见 `reports/data-spike.md`） |
| 原始 / 拒绝 / 精确重复 / 最终 | 701,528 / 0 / 7,276 / **694,252** |
| 用户 / 商品（原始） | 631,986 / 112,565 |
| 正向定义 | rating ≥ 4.0（这是"正向评价"，**不是**点击或购买） |
| 稀疏度 | 平均每个用户 1.11 条评论；评论过 ≥2 次的用户只占 7.66% |
| normalized_sha256 | `539f0a01631878c1d01456aad06447ada6d6ff0588ac3e6e84cddc9ee7801cd8` |

同一份输入跑两次 prepare，`normalized_sha256` 和 parquet 字节都一致；打乱输入的行顺序，结果也一样（DATA-T04）。

## 3. 时间切分
全局时间切分，所有窗口都是左闭右开；k-core 等过滤一概不做。

| 窗口 | 区间（UTC） | 交互 | 用户 | 商品 | warm 用户 | cold 用户 |
|---|---|---|---|---|---|---|
| train | < 2021-05-01 | 546,927 | 502,860 | 88,939 | — | — |
| valid | 2021-05-01 ～ 2022-01-01 | 72,169 | 68,368 | 26,069 | 5,251 | 63,117 |
| test | 2022-01-01 ～ 2023-10-01 | 75,156 | 71,123 | 21,978 | 4,077 | 67,046 |

`split_sha256 = 21a1d1c62badc5a2fa386f808e526273572108d14e621a28172a5ef2cf96def3`，`protocol_sha256 = 0f486b9cadedac9924ea78514f8a4d3ac349c53f61b01255b8031a7000da3efa`

边界的选择理由见 `reports/data-spike.md`：让 valid 和 test 各占约 10%；`test_end` 放在最后一条记录（2023-09-09）之后，所以没有数据被丢弃。

## 4. 评估协议（ADR-0007，原样沿用）
记 `C = train_end`。
- **seen(u)**：u 在 C 之前评论过的**所有**商品，低分的也算。推荐时全部排除。
- **eligible**：在 C 之前至少有一条评论的商品。
- **真值** = 窗口内的正向评价 − seen(u)，再与 eligible 取交集。
- **有效用户**：C 之前有评论，并且真值非空。
- **全量排序**：每个用户都在 `eligible − seen(u)` 上排序，**不做负采样评估**；分数并列时按 item_id 升序。
- **test 评估时 seen 仍然只算到 C**，不加入 valid 窗口的评论。
- 指标：NDCG@10（二元相关）、Recall@10（分母是 |rel|），都是用户级平均；Coverage@10 = Top-10 里出现过的不同商品数 / |eligible|。

评估器用 02 规格里手算的 Golden Case 做测试（TE-T07）。离线评估和 API 共用同一个 `rank()` 函数。

## 5. 结果

### 5.1 validation（用于选模型和设定门禁阈值）
| 模型 | run_id | NDCG@10 | Recall@10 | Coverage@10 | 有效用户 | 回退用户 |
|---|---|---|---|---|---|---|
| **Pop** | `0711a659f7bf41afad75c74d2336ace7` | **0.008416** | **0.015787** | 0.000124 | 2,122 | 0 |
| BPR（选定） | `9c62b80a195642f2a2e9dd455c7e08a2` | 0.004583 | 0.008718 | 0.026372 | 2,122 | 377 |

### 5.2 test（配置冻结后，每个 run **只跑一次**）
| 模型 | run_id | NDCG@10 | Recall@10 | Coverage@10 | 有效用户 | 回退用户 |
|---|---|---|---|---|---|---|
| **Pop** | `0711a659f7bf41afad75c74d2336ace7` | **0.007975** | **0.020264** | 0.000124 | 1,135 | 0 |
| BPR（选定） | `9c62b80a195642f2a2e9dd455c7e08a2` | 0.002961 | 0.007930 | 0.020407 | 1,135 | 236 |

两张表用的是同一个切分、同一个协议、同一套候选规则。test 上两个模型的 `ground_truth_sha256` 都是 `aa7b3a03…49cf`。

### 5.3 所有剔除计数
| 计数 | validation | test |
|---|---|---|
| eligible 商品 | 88,939 | 88,939 |
| 有效用户 | 2,122 | 1,135 |
| 没有真值的 warm 用户（不计入平均） | 3,129 | 2,942 |
| 因为已见而剔除的真值 | 13 | 15 |
| 因为冷商品（不在 eligible）而剔除的真值 | **16,369** | **26,223** |
| BPR 回退到 Pop 的用户（train 中没有正向评价） | 377 | 236 |

两个窗口里，被剔除的冷商品真值都远多于保留下来的真值。这个品类新品多、用户很少回来，基于协同过滤的方法很难发挥作用。冷启动没有放在主报告里评估（`cold_start_report: separate`，v1.2 没有实现）。

## 6. BPR 超参选择（只看 validation）
自己写的 PyTorch BPR，用 SparseAdam，batch 4096，seed 42，最多 50 个 epoch，按 validation NDCG@10 早停（patience 5）。网格共 8 组，8 组的训练时间合计 316 秒（CPU）。

| run | dim | lr | reg | best / 实际跑的 epoch | 训练秒 | val NDCG@10 | val Recall@10 | val Coverage@10 |
|---|---|---|---|---|---|---|---|---|
| `9c62b80a` | 64 | 5e-3 | 1e-4 | 5 / 10 | 51 | **0.004583** | 0.008718 | 0.02637 |
| `a8c39510` | 64 | 5e-3 | 1e-5 | 5 / 10 | 50 | 0.004583 | 0.008718 | 0.02616 |
| `10eb40cd` | 32 | 5e-3 | 1e-5 | 5 / 10 | 42 | 0.003718 | 0.007540 | 0.02114 |
| `5f3a09b5` | 32 | 5e-3 | 1e-4 | 5 / 10 | 42 | 0.003718 | 0.007540 | 0.02131 |
| `b7956693` | 64 | 1e-3 | 1e-5 | 1 / 6 | 34 | 0.002960 | 0.005660 | 0.15109 |
| `21969b00` | 64 | 1e-3 | 1e-4 | 1 / 6 | 34 | 0.002960 | 0.005660 | 0.15109 |
| `6ecbf9b8` | 32 | 1e-3 | 1e-5 | 1 / 6 | 33 | 0.002960 | 0.005660 | 0.12428 |
| `9d81c4c8` | 32 | 1e-3 | 1e-4 | 1 / 6 | 30 | 0.002960 | 0.005660 | 0.12430 |

- **选择规则**：validation NDCG@10 最高者胜出。`9c62b80a` 和 `a8c39510` 的 NDCG 和 Recall 完全相同，按 Coverage 更高来打破并列。
- **lr=1e-3 的 4 组没有训练起来**：6 个 epoch 里损失只从 0.694 降到 0.669，validation NDCG 完全没变，于是被 patience 5 停掉了。拆开看，0.002960 这个数**全部来自走 Pop 回退的 377 个用户**，模型认识的用户贡献几乎为 0。也就是说，这几组 BPR 在被早停时等同于随机。
- **lr=5e-3 的 4 组**：第 5 个 epoch 达到峰值，之后损失继续下降，validation 却下降，属于过拟合。
- 按用户拆分（validation，选定的 BPR）：在 BPR 认识的 1,745 个用户上，BPR 的 NDCG 是 0.00197，Pop 是 0.00663；在回退的 377 个用户上，两者完全相同。
- 配对比较（validation）：BPR − Pop = −0.00383，配对标准误 0.00129，2000 次 bootstrap 的 95% 置信区间是 [−0.00645, −0.00149]。Pop 的 NDCG 标准误是 0.00153；随机排序的期望 NDCG（|rel|=1）约为 0.000051。
- test 上没有再做 bootstrap，因为那需要再看一次 test。test 上的差距（−0.0050）方向和 validation 一致。

## 7. 发布、门禁与服务

### 7.1 门禁阈值（`configs/quality.yaml`，根据 validation 数字设定，用户已批准）
| 阈值 | 值 | 理由 |
|---|---|---|
| `min_abs_ndcg` | 0.004 | 约为随机排序的 80 倍，约等于 Pop 减 2.9 个标准误；能拦住训练坏了或映射错了的 bundle |
| `min_improvement` | 0.0025 | 约为 2 个配对标准误；非 Pop 模型必须明显超过噪声 |
| `max_regression` | 0.0 | 评估是确定性的；同一切分上，新模型不允许比 champion 差 |

### 7.2 门禁结果
| release | 模型 | build | 结果 |
|---|---|---|---|
| `rel-1fd01c0aa3d4` | Pop `0711a659` | `741fa16`，clean | **PASSED**，晋升为 champion（version 1） |
| `rel-afe1b95456a0` | BPR `9c62b80a` | `0338be2`，clean | REJECTED：`NOT_BETTER_THAN_POP`（0.004583 ≤ 0.008416 + 0.0025） |
| `rel-fb924c045eab` | Pop `1ac6483d` | `0539759`，**dirty** | REJECTED：`DIRTY_BUILD` |

最后那个 dirty build 是真实发生的：训练时我把命令输出重定向到了仓库内的目录，shell 在训练开始前就创建了那个文件，于是那次 run 被记成 dirty。之后在干净的工作区上重训了 Pop，所有指标逐位相同。dirty 的 release 留着，作为门禁拒绝的实例。

### 7.3 服务（本地，Windows，单 worker，champion `rel-1fd01c0aa3d4`）
| 项目 | 值 |
|---|---|
| 启动到 ready（Docker） | 约 10 秒（加载 47MB 的 Pop release，其中 50 万个 user_key 的 seen 集合占大头） |
| 服务端延迟（`monitor summary`，200 个请求，top_k=10） | p50 2.05 ms / **p95 2.42 ms** |
| 客户端延迟（含本机 HTTP 往返） | p50 7.05 ms / p95 7.87 ms |
| 隐私扫描 | release、事件库、服务日志对照 631,986 个真实 user_id，命中 0 |

训练耗时：Pop 1.07 秒；选定的 BPR 50.6 秒（10 个 epoch，含每个 epoch 的 validation 评估）；prepare 约 12 秒；split 约 3 秒。

## 8. 局限与诚实声明
1. **评分评论 ≠ 购买**。这里的"正向"是 rating ≥ 4 的评论，不是点击、加购或购买。
2. **没评论 ≠ 不喜欢**。BPR 的负样本只是"没观察到"。
3. **离线 NDCG ≠ 线上 CTR**。系统没有曝光和点击数据，`monitor summary` 里的线上推荐质量一律显示 `UNAVAILABLE`。
4. **BPR 不如 Pop**。在这个数据上，个性化模型没有带来离线收益。结论只适用于本品类和本协议，不能推广成"BPR 无效"。
5. **样本量小**。validation 上 Pop NDCG 的标准误约为 0.0015（2,122 人）；test 上的有效用户只有 1,135 人，噪声会更大（test 上没有算标准误）。小差距不能下结论。
6. **网格的局限**。lr=1e-3 时 patience=5 不够，模型在指标还没开始动之前就被停了。按规格没有改网格和 patience，所以 BPR 的上限可能被低估；但 lr=5e-3 的结果显示，即使训练充分，也会很快过拟合，而且峰值仍然低于 Pop。
7. **冷启动没有处理**。被剔除的冷商品真值数是有效用户数的约 7.7 倍（validation：16,369 / 2,122）和约 23 倍（test：26,223 / 1,135）。这部分需要内容特征（v1.1 的融合设计），v1.2 不在范围内。
8. **单机演示**。v1.2 有意省略了部署核验、双密钥、Drift 监控和反馈重训，设计见 `docs/specs/` 里的 v1.1 文档和 ADR-0009。
9. **哈希记录的一个瑕疵**：`reports/test_runs.log` 里记录的 `evaluation_sha256` 是 Windows 上写出的 CRLF 版文件的哈希；Git 里保存的是 LF 版本。两次正式评估都是在同一份代码上跑的，跑完之后才修复，修复后所有 JSON 都固定写 LF。指标不受影响。clone 下来的 LF 版文件对应的哈希如下：

| run | 日志里记录的（CRLF） | Git 里的文件（LF） |
|---|---|---|
| `0711a659` | `731a2e0c…7a92` | `235f8597…7f66` |
| `9c62b80a` | `cf290de1…1a69` | `53c30a18…4425` |

## 9. 复现
```bash
python scripts/download_data.py --category All_Beauty
export RP_USER_KEY=...            # any secret of >= 16 chars; keep it for packaging/serving
reviewpulse data prepare && reviewpulse data split
reviewpulse train --model Pop
reviewpulse train --model BPR --grid
reviewpulse package --run-id <run_id>
reviewpulse gate --release-id <release_id>
reviewpulse promote --release-id <release_id> --expected-version 0 --execute
```
换一把 `RP_USER_KEY` 会改变所有 `user_key`，因此会生成不同的 release_id，但所有指标都不变。test 评估已经冻结并执行过（`reports/frozen_config.json`、`reports/test_runs.log`），不应该再跑。

# 验收清单（测试 ID → 任务 → 规格）

| 测试 ID | 内容 | 任务 | 规格 |
|---|---|---|---|
| DATA-T01 | 重复记录去重确定 | T02 | 01 |
| DATA-T02 | 非法评分 / 时间被拒 | T02 | 01 |
| DATA-T03 | 未声明 timestamp 单位报错 | T02 | 01 |
| DATA-T04 | prepare 哈希可重现、与行顺序无关 | T02 | 01 |
| TE-T01 | 注入未来评论不改变 train 窗口 | T03 | 01 |
| TE-T03 | 只在 test 出现的用户不算 warm | T03 | 01 |
| TE-T07 | Golden Case 数字逐项相等 | T04 | 02 |
| TE-T08 | 低分已见商品不被推荐 | T04 | 02 |
| EVAL-T01 | `|rel|=0` 用户不进平均且计数 | T04 | 02 |
| EVAL-T02 | NaN / 0 有效用户时报错 | T04 | 02 |
| REC-T01 | Pop 分数与排序 | T05 | 03 |
| REC-T02 | BPR fixture 训练损失有限 | T06 | 03 |
| REC-T03 | 保存 → 新进程加载结果一致 | T06 | 03 |
| REC-T04 | 无已见、无重复、排名连续 | T04 | 03 |
| REC-T05 | 未知用户 Pop 回退 | T04 | 03 |
| REC-T06 | NaN / Inf 分数报错 | T06 | 03 |
| REL-T01 | 两个新进程推荐一致 | T08 | 04 |
| REL-T02 | 换映射加载失败 | T08 | 04 |
| REL-T03 | 模型哈希不符加载失败 | T08 | 04 |
| REL-T04 | 密钥缺失 / 错误加载失败 | T08 | 04 |
| REL-T05 | release 中无原始 user_id | T08 | 04 |
| GATE-T01 | 低于阈值被拒，champion 不变 | T09 | 04 |
| GATE-T02 | 协议哈希不同被拒 | T09 | 04 |
| GATE-T03 | BPR 不如 Pop 被拒，Pop 通过 | T09 | 04 |
| GATE-T04 | dirty build 被拒 | T09 | 04 |
| PROM-T01 | dry-run 不写 | T10 | 04 |
| PROM-T02 | 首次晋升 expected=0 | T10 | 04 |
| PROM-T03 | 并发晋升只有一个成功 | T10 | 04 |
| PROM-T04 | A→B→A 后过期版本 CONFLICT | T10 | 04 |
| PROM-T05 | 回滚到未晋升过的 release 被拒 | T10 | 04 |
| API-T01 | import 不加载模型 | T11 | 05 |
| API-T02 | 无 champion 的三个接口行为 | T11 | 05 |
| API-T03 | 真实 release ready 并推荐 | T11 | 05 |
| API-T04 | 不重启则 loaded_release_id 不变 | T11 | 05 |
| API-T05 | 参数错误 422 ApiError | T11 | 05 |
| API-T06 | u1 推荐不含 a、b | T11 | 05 |
| API-T07 | 未知用户 fallback | T11 | 05 |
| MON-T01 | 每请求恰好一条事件 | T12 | 05 |
| MON-T02 | 事件中无原始 user_id | T12 | 05 |
| MON-T03 | 事件 release 与 /ready 一致 | T12 | 05 |
| MON-T04 | 事件写失败不影响推荐 | T12 | 05 |
| OPS-T01 | fresh clone 快速开始可跑（CI E2E） | T13a / T16 | 06 |
| OPS-T02 | Docker 无 champion 可启动 | T13b | 06 |
| OPS-T03 | 仓库无数据 / 密钥 / 状态文件 | T13a | 06 |
| OPS-T04 | test 正式评估只一次 | T14 | 06 |
| SENT-T01..03 | 情感加分项 | T17 | 07 |

共 45 项必做测试（不含 SENT）。测试名建议直接带 ID，例如 `test_TE_T07_golden_case`。

# 04 Release 包、质量门禁、晋升与回滚

## REL-01 Release 目录
```text
releases/<release_id>/
  manifest.json          # schemas/release-manifest.v1.2.schema.json
  model.pt | pop.json
  user_mapping.json      # 键 = user_key
  item_mapping.json
  seen_items.json        # 键 = user_key，值 = 商品列表（只到 C）
  eligible_items.json
  pop_fallback.json      # Pop 排序后的 [item_id, score] 列表（见 REL-01 补充）
  evaluation.json        # 验证集评估 manifest
```
- `user_key = HMAC-SHA256(RP_USER_KEY, user_id)` 的十六进制。密钥来自环境变量，不进 Git、不进 manifest。
- Manifest 记 `user_key_id = HMAC-SHA256(RP_USER_KEY, "reviewpulse/key-id/v1")` 前 16 位，用来启动时确认密钥没换错。
- Manifest 里每个文件都有 sha256；`release_id = "rel-" + sha256(去掉 release_id 和 created_at_utc 后的规范化 manifest)[:12]`。
- `build.git_dirty` 为 true 时可以打包（开发用），但门禁一律拒绝（`DIRTY_BUILD`）。

## REL-02 加载与校验（`release/loader.py`，服务与门禁共用）
任一失败都抛 `BundleInvalid(reason)`：文件缺失、哈希不符、映射维度与模型不符、`RP_USER_KEY` 缺失或指纹不符、smoke 推理（对一个已知用户和一个未知用户各推荐一次）失败。

## GATE-01 门禁（`registry/gate.py`）
输入只有 `release_id`，所有数字从 release 里的 `evaluation.json` 读（C-05）。按顺序检查，第一个失败即 `REJECTED(reason)`：

| 顺序 | 检查 | 失败 reason |
|---|---|---|
| 1 | Release 能完整加载（REL-02） | `BUNDLE_INVALID` |
| 2 | `git_dirty == false` | `DIRTY_BUILD` |
| 3 | `eval_split == validation`，指标有限 | `INVALID_METRICS` |
| 4 | `ndcg_at_10 >= min_abs_ndcg`（`configs/quality.yaml`） | `BELOW_MIN` |
| 5 | 非 Pop：与 Pop 基线的 evaluation 三个哈希一致，且 `ndcg_at_10 > pop_ndcg + min_improvement` | `PROTOCOL_MISMATCH` / `NOT_BETTER_THAN_POP` |
| 6 | 已有 Champion：与 Champion 的 `split_sha256`、`protocol_sha256` 一致（不一致 → `SPLIT_MISMATCH`），且 `ndcg_at_10 >= champion_ndcg - max_regression` | `SPLIT_MISMATCH` / `REGRESSION` |

- `kind = Pop` 免检第 5 条（它就是基线）。所以 BPR 输给 Pop 时，可以晋升 Pop 保证服务可用，并在报告中写明 BPR 未通过。
- `min_abs_ndcg`、`min_improvement`、`max_regression` 在 Day 2 看到真实数字后写进 `quality.yaml`，并记录理由；未配置时 `--execute` 拒绝执行。
- 门禁结果写成 `gate_decision.json`（含输入哈希、每条检查结果），其 sha256 写进晋升记录。

### v1.2 补充（2026-11-23 用户批准，Day 3 实现前）
- **REL-01 `pop_fallback.json` 格式**：按 Pop 顺序（分数降序、`item_id` 升序）排列的 `[item_id, score]` 列表，score 为整数（C 之前正向评价的去重用户数）。必须存分数，因为 `rank()` 按分数排序，API 也要返回真实分数。商品集合必须与 `eligible_items.json` 相同，加载时校验。
- **GATE-01 第 5 条的 Pop 基线来源**：门禁只接受 `release_id`，所以 Pop 基线 = `releases/` 中 `kind=Pop`、且 `split_sha256` / `protocol_sha256` / `ground_truth_sha256` 三个哈希都与候选一致、并能完整加载的 release，用它的 `evaluation.json`（仍满足 C-05：只读登记的评估文件）。找不到 → `PROTOCOL_MISMATCH`，detail 写明"没有同口径的 Pop 基线"。不把 Pop 评估塞进候选 release（manifest schema 的 `files` 不允许额外条目）。
- **`gate_decision.json` 的位置**：写到 `state/gate/<release_id>.json`，不写进 release 目录（release 目录在打包后保持不可变，所有文件都受 manifest 哈希约束）。

## PROM-01 Champion 存储（`state/registry.db`，SQLite）
```sql
CREATE TABLE champion (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  release_id TEXT, version_counter INTEGER NOT NULL,
  previous_release_id TEXT, promoted_at_utc TEXT, actor TEXT, gate_decision_sha256 TEXT
);
INSERT INTO champion (id, version_counter) VALUES (1, 0);   -- 初始化时预置
CREATE TABLE promotion_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, time_utc TEXT, action TEXT,  -- PROMOTE / ROLLBACK
  release_id TEXT, expected_version INTEGER, status TEXT, reason TEXT, actor TEXT
);
```

## PROM-02 晋升 `promote(release_id, expected_version, execute=False, actor)`
1. 跑门禁；失败 → `REJECTED`，写 log，不动 champion。
2. `execute=False` → 返回 `DRY_RUN` 和门禁结果。
3. `execute=True` → 一个事务里：
   `UPDATE champion SET release_id=?, version_counter=version_counter+1, previous_release_id=release_id, ... WHERE id=1 AND version_counter=?`
   影响行数 0 → `CONFLICT`；否则 `PROMOTED`。
4. 首次晋升 `expected_version=0`。用版本号而不是 release_id 做比较，所以 A→B→A 回滚后，拿着旧版本号的请求一定失败。

## PROM-03 回滚 `rollback(release_id, expected_version, execute=False, actor)`
- 目标必须在 `promotion_log` 中出现过 `PROMOTED`，且现在仍能完整加载（REL-02）；不重跑比较门禁。
- 同一个 CAS 更新；状态 `ROLLED_BACK`。

## PROM-04 生效方式
晋升 / 回滚只改指针。**重启服务**后才加载新 release；用 `GET /ready` 的 `loaded_release_id` 确认。README 的运行手册写清这三步。

## 验收
- `REL-T01` 同一 release 两个新进程推荐结果一致
- `REL-T02` 换掉 `item_mapping.json` → 加载失败
- `REL-T03` 模型文件哈希不符 → 加载失败
- `REL-T04` 缺 `RP_USER_KEY` 或密钥换了 → 加载失败（`USER_KEY_UNAVAILABLE`）
- `REL-T05` 扫描 release 目录，找不到任何原始 user_id
- `GATE-T01` 低于阈值被拒，champion 表字节不变
- `GATE-T02` 与 Pop 协议哈希不同被拒
- `GATE-T03` BPR 不如 Pop 被拒；Pop 通过（v1.1 PROM-T11）
- `GATE-T04` dirty build 被拒
- `PROM-T01` dry-run 不写表
- `PROM-T02` 首次晋升 `expected=0` 成功，版本变 1
- `PROM-T03` 两个并发晋升（两个线程 / 进程，同一 expected）只有一个成功
- `PROM-T04` A→B→A 回滚后，用过期版本号晋升返回 `CONFLICT`
- `PROM-T05` 回滚到没晋升过的 release 被拒

# 07 加分项：弱标签情感分类（Day 5 有余力才做）

**定位**：离线实验，证明同一套 MLflow / 时间切分基础设施能承载第二种任务。**不进 Release，不进 API，不做融合。**

## SENT-01 任务
- 输入评论 `text`，预测 `negative` / `positive`。
- 弱标签：`rating <= 2` → negative，`rating >= 4` → positive，`rating == 3` 不参与。**星级代理标签不是人工情感真值**，报告里写明。
- 无文本的评论丢弃并计数。

## SENT-02 切分
- 只用 `train_end` 之前的评论；在其内部按时间再切：前 85% 训练、后 15% 验证（时间顺序，不随机）。
- 不使用推荐 valid/test 窗口的评论（为以后做融合特征留出干净口径，沿用 v1.1 ADR-0007 第 8 条）。

## SENT-03 模型与指标
- `TfidfVectorizer(max_features=50000, ngram_range=(1,2))` + `LogisticRegression(class_weight="balanced")`。
- 指标：`sent/val/macro_f1`、各类 support、混淆矩阵；对照基线：全部预测多数类。
- 记一个 MLflow run（task=sentiment）。

## 验收
- `SENT-T01` 3 分评论不进训练集；弱标签阈值正确
- `SENT-T02` 同 seed 训练两次 macro_f1 相同
- `SENT-T03` 训练 / 验证集中没有 `train_end` 之后的评论

## 若不做
README 的 "Future work" 写一句："情感特征融合已有 as-of 口径设计（v1.1 05/07），未实现。"

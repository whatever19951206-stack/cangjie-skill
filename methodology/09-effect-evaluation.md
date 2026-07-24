# 阶段 4.5 — 真实效果评测

## 目标

阶段 4 的压力测试证明 Skill 内部路由和执行合同能够通过预设测试。阶段 4.5 进一步验证：

> 同一个模型加载 Skill 后，是否比不加载 Skill 的 baseline 更好？

没有 uplift 的 Skill，即使格式完整、证据正确，也可能不值得长期维护。

## 三套评测

### 1. Routing

给模型用户 prompt 和候选 Skills，记录最终选择的 Skill，或选择不调用。

必须包含：

- 隐式触发场景
- 不应调用任何 Skill 的负例
- 同书兄弟 Skill 的混淆场景
- 多意图和边界场景

评测器输出 accuracy、macro F1、逐 Skill precision / recall / F1 和混淆矩阵。

### 2. Execution

在明确要求使用目标 Skill 的前提下，对 baseline 与 Skill 回答使用同一套加权 rubric。

Rubric 应来自 `skill.yaml.workflow` 和 `output_contract`，而不是根据某次回答临时编写。

### 3. Faithfulness

使用 `evidence.json` 审计：

- 作者观点是否有支持证据
- 模型推断是否被正确标记
- 是否出现不存在或不允许的 evidence ID
- 是否产生 unsupported claims 或 citation errors

## 公平 A/B

每个 case 必须使用相同：

- `model_id`
- 模型参数
- 工具权限
- 输入材料
- 上下文长度
- 评审 rubric

唯一变量应是是否加载目标 Skill。

## 隐藏测试

正式 hidden cases 不能暴露给生成 Skill 的 agent，也不应提交到公开仓库。报告只保留 case ID、分数和通过状态，不复制 prompt 或原始回答。

## 评审独立性

优先使用未参与 Skill 生成的独立 agent 或人工评审。评审结果写入结构化 JSONL，而不是由被测模型自报。

## 执行

```bash
python scripts/evaluate_skill.py \
  --cases evals/cases.jsonl \
  --results evals/results.jsonl \
  --output evals/report.json \
  --markdown-output evals/report.md \
  --minimum-uplift 0.05 \
  --fail-on-regression
```

## 结果判断

- uplift 明显为正、无关键回归：可以进入发布候选
- 总体 uplift 为正但某套 suite 回归：修复后重跑
- uplift 接近 0：考虑合并、降级为模板或删除
- uplift 为负：不得仅调整测试集掩盖问题

## 不能证明的事情

评测 harness 只能保证数据合同、配对、公平性检查和统计正确。它不能自动保证 evaluator 的判断正确，因此正式发布仍应保留人工抽检和 evaluator 版本记录。

# P1：真实效果评测

P0 证明一个 Skill bundle 在内部是一致、可追溯、经过真实测试的。P1 回答更重要的问题：

> 加载 Skill 后，同一个模型是否真的比不加载 Skill 表现更好？

## 评测分层

P1 把评测拆成三套，避免用单一“通过率”掩盖不同问题。

### Routing

判断应该调用哪个 Skill，或是否应该不调用任何 Skill。

输出包括：

- accuracy
- macro F1
- 每个 Skill 的 precision / recall / F1
- baseline 与 Skill 条件的混淆矩阵
- 从正确变错误的回归案例

### Execution

在已确定应调用某个 Skill 后，独立评审员按加权 rubric 为回答打分。每个 rubric 权重之和必须是 1。

典型 rubric：

- 是否定义了明确的失败状态
- 是否覆盖关键失败路径
- 是否把路径转化为可执行动作
- 是否遵循判停条件和输出合同

### Faithfulness

检查输出是否忠于证据链：

- `faithfulness_score`
- 支持与不支持的主张数量
- 引用错误数量
- 使用的 evidence IDs 是否在允许集合中

模型推断可以存在，但不能伪装成作者明确观点。

## 公平 A/B 合同

每个 case 必须同时有：

- `condition: baseline`：同一个模型，不加载目标 Skill
- `condition: skill`：同一个模型，加载目标 Skill

评测器强制两边 `model_id` 一致。温度、工具权限、上下文材料和系统环境也应保持一致，并在外部运行记录中保存。

本仓库的 harness 不负责调用具体模型，也不让模型自动给自己判分。它只汇总已经由独立 agent 或人工评审产生的结构化 annotations。

## 文件格式

### Cases

JSONL，每行一个 case，遵循 `schemas/eval-case.schema.json`：

```json
{"id":"routing-001","suite":"routing","prompt":"……","visibility":"public","expected":{"expected_skill":"failure-preflight","candidate_skills":["failure-preflight","checklist-review"]}}
```

### Results

JSONL，每个 case 必须有 baseline 和 skill 两条结果，遵循 `schemas/eval-result.schema.json`：

```json
{"id":"routing-001","suite":"routing","condition":"baseline","model_id":"model-x","evaluator_id":"judge-y","response_sha256":"sha256:...","selected_skill":"checklist-review"}
```

`response_sha256` 用于把 annotation 与原始回答关联。原始回答可以保存在受控评测系统中，不必进入公开报告。

## 隐藏测试

`visibility: hidden` 的 case 在报告中只显示 ID、分数和是否通过，prompt 不会被复制到 JSON 或 Markdown 报告。

注意：把 hidden case 提交到公开 Git 仓库后，它就不再是真正保密。正式隐藏集应存放在：

- 私有仓库
- CI 的受限下载位置
- 评测平台的私有数据集
- 本地不提交目录

`examples/evaluation-harness/` 中的 hidden 标签只是演示报告脱敏行为。

## 运行

```bash
python scripts/evaluate_skill.py \
  --cases evals/cases.jsonl \
  --results evals/results.jsonl \
  --output evals/report.json \
  --markdown-output evals/report.md
```

增加发布门槛：

```bash
python scripts/evaluate_skill.py \
  --cases evals/cases.jsonl \
  --results evals/results.jsonl \
  --output evals/report.json \
  --minimum-uplift 0.05 \
  --fail-on-regression
```

退出码：

- `0`：格式正确且效果门槛通过
- `1`：数据有效，但 uplift 或 regression 门槛失败
- `2`：Schema、配对、rubric 或公平 A/B 合同无效

## Uplift

每套评测先独立计算 baseline 与 Skill 分数：

- Routing：accuracy
- Execution：加权 rubric 平均分
- Faithfulness：独立评审给出的 faithfulness score

整体分数对存在的 suite 等权平均：

```text
uplift = skill_score - baseline_score
```

报告会保留每个 suite 的单独 uplift。不要只看总体数字；Routing 上升而 Faithfulness 下降仍然需要处理。

## 示例

```bash
python scripts/evaluate_skill.py \
  --cases examples/evaluation-harness/cases.jsonl \
  --results examples/evaluation-harness/results.jsonl \
  --output /tmp/cangjie-eval-report.json \
  --markdown-output /tmp/cangjie-eval-report.md \
  --minimum-uplift 0.5 \
  --fail-on-regression
```

示例故意让 baseline 在路由、执行完整性和忠实度上较弱，用于验证统计、混淆矩阵、隐藏 prompt 脱敏和门禁行为。

## 局限

P1 harness 能防止配对错误、统计错误和报告泄露，但不能保证评审员本身正确。正式使用仍应：

- 使用未参与 Skill 生成的评审员
- 固定 rubric
- 对一部分案例进行人工复核
- 记录 evaluator 版本
- 定期检查评审员一致性

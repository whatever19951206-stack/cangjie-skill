# 阶段 2–5 补充：机器质量门禁

## 目标

把“请 Agent 严格遵守”升级成可执行的文件合同和 CI 门禁。人类可读的 `SKILL.md` 仍然保留，但发布判断以机器 bundle 的完整性为必要条件。

## 每个 Skill 的机器 bundle

```text
<skill-dir>/
├── skill.yaml
├── evidence.json
├── test-prompts.json
└── test-results.json
```

对应 Schema 位于 `schemas/`。

## skill.yaml

`skill.yaml` 是路由和执行合同，至少包含：

- 稳定名称与来源信息；
- `routing.include` / `routing.exclude`；
- 可执行 `workflow` 与完成标准；
- `boundaries`；
- `evidence_refs`；
- 验证分数、风险等级和发布状态。

阶段 2 创建时必须是：

```yaml
verification:
  hard_gate_passed: false
status: draft
```

只有真实证据和测试都通过后才能修改。

## evidence.json

每项证据必须包含：

- 稳定 `evidence_id`；
- `claim_type`；
- 可回查的 `source_ref`；
- 证据文本；
- 文本 SHA-256。

`claim_type` 的语义：

- `direct_quote`：原内容逐字引用；
- `author_paraphrase`：忠实转述作者观点；
- `inferred_method`：从多个材料归纳出的模型推断；
- `model_critique`：模型或编辑者的批判；
- `external_fact`：来自原内容之外、需单独核验的事实。

不得把后三类包装成作者明确主张。

## test-results.json

测试题和测试结果必须分开保存。`test-results.json` 只能记录实际执行结果，并标注执行模式：

- `independent-agent`
- `main-agent-fallback`
- `manual`

不得根据 `expected_behavior` 自动填写“通过”。

## 自动检查

单个 bundle：

```bash
python scripts/quality_gate.py path/to/skill
```

整个仓库：

```bash
python scripts/quality_gate.py --all
```

门禁检查：

- JSON Schema；
- 未清理占位符；
- 证据 ID 与引用一致性；
- 来源和哈希一致性；
- 测试数量与兄弟 Skill 负例；
- 逐条结果与汇总一致性；
- 负例零容错；
- tested / published 状态必须对应通过结果。

## 失败处理

- 路由失败：修 A2、`routing` 或 sibling priority；
- 执行失败：修 E / workflow 与完成标准；
- 忠实度失败：修证据、claim_type 或方法论解释；
- 负例失败：不得用提高总通过率掩盖，必须修复误触发；
- 缺少结果：执行测试，不能生成一个“看起来通过”的文件。

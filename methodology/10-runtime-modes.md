# 运行模式、Map-Reduce 与增量续跑

## 为什么需要运行模式

不同材料和使用风险不应走同样重的流程。固定启动五个全文 Agent 会造成重复读取、后半程降质和不必要的成本。

## 模式选择

### Scan

用于回答“这份材料值不值得加工”。只启用框架和原则提取器，限制候选和 Skill 数量，不允许标记正式发布。

### Standard

个人长期复用的默认模式。启用全部提取器、证据链、独立评审和公开 A/B 评测。

### Audit

组织级或高风险使用。除 Standard 要求外，必须加入外部事实核验、真正隐藏的测试集和完整审计记录。

模式配置以 `project.yaml` 为机器来源，不得只在对话中口头约定。

## Map-Reduce

### Map

每个 chunk 由选定的 extractor 独立处理，只输出局部观察、证据定位和候选，不直接决定最终 Skill。

### Reduce

同一 extractor 的所有局部输出被归并、去重和冲突检查。

### Merge

不同 extractor 的输出建立关联：方法、案例、反例和术语不能作为互不相干的清单存在。

### Verify

对合并候选执行 V1/V2/V3、风险要求和候选数量限制，再进入用户确认与 Skill 构造。

## 缓存键

缓存只能在影响结果的输入完全相同时复用。至少包含：

- source hash
- chunk hash
- extractor
- prompt version
- model ID
- 上游 cache keys
- 模式与质量要求

仅有相同 task ID 不足以复用结果。

## 断点续跑

`pipeline-state.json` 记录：

- completed / pending tasks
- task cache key
- artifact
- model / prompt version
- 当前阶段和状态

成功任务必须同时满足 cache key 相同和 artifact 存在。否则重新执行。

## 导出

导出器是 staging 工具，不应绕过质量门禁。默认只导出通过 P0 的完整 bundle，并记录目标布局和文件哈希。

平台适配应保持薄层：核心 Skill 语义仍在 `SKILL.md`、`skill.yaml`、`evidence.json` 和评测文件中，不为某个平台复制一套不可审计的内容。

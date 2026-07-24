---
name: cangjie-skill
description: Distill a book, long-video transcript, podcast, course, interview, long article, or document set into a small, evidence-backed, executable set of Agent Skills. Use only when the user explicitly asks to "拆书" / "蒸馏内容成 skill" / "turn this source into skills" or wants reusable methods extracted from long-form material. Do not use for ordinary summaries, book reviews, factual Q&A, or role-playing as the author.
---

# cangjie-skill — 长内容到可执行 Agent Skills 的工程化流水线

## 使命

把长内容中的方法论、框架、原则和清单，转化成一组：

- 可在真实场景中正确触发；
- 有明确执行步骤和边界；
- 能回查原始证据；
- 经真实测试而非只生成测试题；
- 相比不用 Skill 的 baseline 有可测提升；
- 可断点续跑、增量更新和跨平台导出的 Skills。

本文中的“书”泛指书籍、视频转写、播客文字稿、课程、访谈、长文和资料集。

## 不适用场景

- 普通摘要、书评、读后感；
- 只需要查一个事实或解释一段内容；
- 模仿作者语气或人格；
- 没有可访问原文，却要求凭记忆蒸馏；
- 一次性、低复杂度、无需长期复用的任务。

## 三种运行模式

开始前根据目标选择模式，并写入 `project.yaml`。模式不是口头约定。

| 模式 | 用途 | 默认上限 | 质量要求 |
|---|---|---:|---|
| `scan` | 快速判断材料是否值得加工 | 3 个 Skills | 轻量提取，不作为正式发布 |
| `standard` | 个人长期复用 | 5 个 Skills | 完整证据、独立测试、公开 A/B |
| `audit` | 组织级或高风险场景 | 10 个 Skills | 外部核验、隐藏评测、完整审计 |

模式细则见 `methodology/10-runtime-modes.md`。

## 总体流水线

```text
预检与模式选择
  ↓
阶段 0  整体理解
  ↓
阶段 1  Map-Reduce 并行提取
  ↓
阶段 1.5  候选验证、去重、用户轻确认
  ↓
阶段 2  RIA++ Skill 构造 + 机器证据链
  ↓
阶段 3  Skill 关系、术语和兄弟路由
  ↓
阶段 4  路由与执行压力测试
  ↓
阶段 4.5  baseline vs Skill 真实效果评测
  ↓
阶段 5  全项目门禁、交付和导出
```

## 输入要求

开始前必须确认：

1. **原始内容**：可访问的 PDF、EPUB、TXT、字幕、转写稿或纯文本。
2. **元信息**：标题、作者或讲者、年份或发布时间、内容类型。
3. **使用目标**：生成的 Skills 准备用于哪些真实任务。
4. **风险级别**：是否涉及医疗、法律、财务、人事、安全或组织制度。
5. **模式**：`scan`、`standard` 或 `audit`。

不要在缺少原始文本时继续。不要把用户目标当作修改作者原意的依据。

## 项目初始化

推荐使用 CLI 建立项目，而不是临时创建散乱文件：

```bash
python scripts/cangjie.py init books/<slug> \
  --project-id <slug> \
  --mode standard \
  --source-id <stable-source-id> \
  --source-title "<TITLE>" \
  --source-type book \
  --source-file /path/to/source.txt \
  --goal "<真实使用目标>"
```

项目结构：

```text
books/<slug>/
├── project.yaml
├── pipeline-state.json
├── BOOK_OVERVIEW.md
├── verified.md
├── INDEX.md
├── GLOSSARY.md
├── DIGEST.md
├── source/
│   └── chunks.jsonl
├── work/
│   └── extraction-plan.json
├── candidates/
├── rejected/
├── skills/
│   └── <skill-slug>/
│       ├── SKILL.md
│       ├── skill.yaml
│       ├── evidence.json
│       ├── test-prompts.json
│       ├── test-results.json
│       └── test-results.md
├── evals/
├── reports/
└── dist/
```

Markdown 是人类可读层；YAML / JSON 是机器状态、证据和质量合同。两者都必须保留。

## 阶段 0 — 整体理解

1. 读取完整材料；长文本先稳定分块。
2. 按 `methodology/01-stage0-adler.md` 完成结构、解释、批判和应用分析。
3. 生成 `BOOK_OVERVIEW.md`。
4. 向用户展示主旨、结构、术语、局限和建议重点。
5. 得到用户确认后再进入提取阶段。

## 阶段 1 — Map-Reduce 提取

先分块：

```bash
python scripts/cangjie.py chunk books/<slug> \
  --source-file /path/to/source.txt \
  --max-chars 12000
```

再生成确定性计划：

```bash
python scripts/cangjie.py plan books/<slug> \
  --prompt-version extractors-v1 \
  --model-id <model-id>
```

### Map

每个 chunk 由选定 extractor 处理：

- framework
- principle
- case
- counter-example
- glossary

`scan` 只启用 framework 和 principle；其他模式启用全部五类。

Map 输出只记录局部观察、证据定位和候选，不直接决定最终 Skill。

### Reduce

同一 extractor 的全部 Map 输出进行：

- 同义归并；
- 重复删除；
- 冲突识别；
- 证据聚合；
- 局部遗漏检查。

### Merge 与 Verify

把方法、案例、反例和术语建立关系，再进入候选验证。

任务完成后必须记录 artifact：

```bash
python scripts/cangjie.py record-task books/<slug> \
  --task-id <task-id> \
  --status success \
  --artifact <relative-artifact-path> \
  --model-id <model-id>
```

缓存只有在 source、chunk、prompt、model 和上游依赖的 cache key 均相同时才可复用。

## 阶段 1.5 — 候选验证与确认

按 `methodology/03-stage1.5-triple-verify.md` 执行：

- **V1 证据多样性**：是否有至少两处相对独立证据？
- **V2 迁移能力**：能否处理原文未直接回答的新问题？
- **V3 增量价值**：是否超出泛泛常识，值得成为独立 Skill？

通过项写入 `verified.md`；淘汰项写入 `rejected/` 并保留理由。

向用户展示：

- 建议保留的候选；
- 建议合并的候选；
- 建议淘汰的候选；
- 证据强度和风险。

得到轻确认后再构造完整 Skills。

## 阶段 2 — RIA++ 构造与证据链

每个候选同时生成：

- `SKILL.md`：人类可读说明；
- `skill.yaml`：机器可读路由、工作流、边界和状态；
- `evidence.json`：来源位置、claim type 和文本哈希。

RIA++：

- **R**：原文与精确位置；
- **I**：方法论骨架；
- **A1**：原内容中的应用；
- **A2**：未来触发条件与相邻 Skill 区分；
- **E**：执行步骤、完成标准和判停条件；
- **B**：不适用场景、失败模式和局限。

主张必须标记：

- `direct_quote`
- `author_paraphrase`
- `inferred_method`
- `model_critique`
- `external_fact`

不得把模型推断或批判写成作者明确观点。

阶段 2 初始状态：

```yaml
verification:
  hard_gate_passed: false
status: draft
```

详见 `methodology/08-machine-quality-gates.md`。

## 阶段 3 — 链接与路由

按 `methodology/05-stage3-zettelkasten.md`：

1. 建立 depends-on、contrasts-with、composes-with；
2. 生成 `INDEX.md` 和 `GLOSSARY.md`；
3. 更新 `skill.yaml.routing.sibling_priority`；
4. 明确“何时用本 Skill，何时优先用兄弟 Skill”。

## 阶段 4 — 压力测试

按 `methodology/06-stage4-pressure-test.md`：

- 至少 3 条 `should_trigger`；
- 至少 2 条 `should_not_trigger`；
- 至少 1 条 `edge_case`；
- 至少 1 条显式 `sibling_skill` 混淆负例。

测试由未参与蒸馏的独立 Agent 或人工执行。不要把预期答案给评测者。

实际结果写入 `test-results.json`。只生成 `test-prompts.json` 不等于经过测试。

负例零容错。通过后才能设置：

```yaml
verification:
  hard_gate_passed: true
status: tested
```

执行门禁：

```bash
python scripts/quality_gate.py books/<slug>/skills/<skill-slug>
```

## 阶段 4.5 — 真实效果评测

按 `methodology/09-effect-evaluation.md`，使用同一个模型比较：

- `baseline`：不加载目标 Skill；
- `skill`：加载目标 Skill。

评测拆分为：

- Routing
- Execution
- Faithfulness

运行：

```bash
python scripts/evaluate_skill.py \
  --cases books/<slug>/evals/cases.jsonl \
  --results books/<slug>/evals/results.jsonl \
  --output books/<slug>/reports/evaluation.json \
  --markdown-output books/<slug>/reports/evaluation.md \
  --minimum-uplift 0.05 \
  --fail-on-regression
```

正式隐藏题不能提交到公开仓库，也不能暴露给生成 Skill 的 Agent。

如果 uplift 接近 0 或为负，考虑修订、合并、降级为模板或删除该 Skill。

## 阶段 5 — 门禁、交付与导出

全项目校验：

```bash
python scripts/quality_gate.py --all
python scripts/cangjie.py validate books/<slug>
```

只有通过证据、测试和效果要求的 Skills 才进入发布候选。

导出示例：

```bash
python scripts/cangjie.py export books/<slug>/skills/<skill-slug> \
  --target generic \
  --output books/<slug>/dist
```

目标：

- `generic`
- `claude`
- `cursor`
- `codex`

所有导出都是 staging。Codex 导出不会假设产品专用的全局安装路径，而会生成中性目录和安装说明。

## 断点续跑

查看状态：

```bash
python scripts/cangjie.py status books/<slug>
```

以 `pipeline-state.json` 为机器状态源。只有 cache key 一致且 artifact 存在的任务才能复用。

不要只根据“文件存在”判断任务完成。

## 旧产物迁移

预览：

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill
```

写入保守脚手架：

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill --write
```

迁移器不会伪造 `test-results.json`，并保持 `hard_gate_passed: false`。

## 质量红线

1. 没有原文不得蒸馏。
2. 每个正式 Skill 必须有完整机器 bundle。
3. 证据必须可定位，哈希必须匹配。
4. 作者观点、推断、批判和外部事实必须分层。
5. 路由必须同时说明 include、exclude 和兄弟优先级。
6. 必须真实执行正例、负例、边界例和兄弟混淆测试。
7. 所有负例必须通过。
8. 测试题不能替代测试结果。
9. `tested` / `published` 必须对应通过的机器结果。
10. Standard / Audit 发布前必须检查 baseline→Skill uplift。
11. Audit 模式必须有外部核验和真正私有的隐藏评测。
12. 质量门禁失败时不得安装或发布。

## 生态定位

- **nuwa-skill**：蒸馏人的表达和思维特征；
- **cangjie-skill**：蒸馏长内容中的可执行方法；
- **darwin-skill**：对已有 Skill 做迭代进化。

`test-prompts.json` 保持 Darwin 兼容；Cangjie 额外提供证据、真实结果、A/B uplift、状态和导出合同。

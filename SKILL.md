---
name: cangjie-skill
description: Distill a book, long-video transcript, podcast, course, or interview into a coherent set of executable skills. Use when the user asks to "拆书" / "蒸馏一本书" / "把 XX 书做成 skill" / "把这个视频/播客/课程蒸馏成 skill" / "turn a book or video into skills" — i.e. wants the frameworks, principles, and methodologies in long-form content extracted into atomic, reusable Claude skills that an agent can invoke in real-world situations. NOT for simple summarization, book reviews, or role-playing as the author (that is nuwa-skill's job).
---

# cangjie-skill — 把一本书蒸馏成一组可执行 skills 的元 skill

## 使命

把一本书里沉淀的方法论，拆解成一组**原子化、可被 agent 在真实场景下调用、可追溯、可测试**的 skills，让读者真正用起来。

> **术语约定**：本文档及 `methodology/`、`extractors/` 中所有的“书”，泛指一切被蒸馏的长内容——书籍、长视频转写、播客文字稿、课程、访谈、长文、资料集。

**边界**：
- ✅ 做：方法论 / 决策框架 / 清单 / 原则 / 概念体系的蒸馏
- ❌ 不做：书摘 / 读后感 / 作者人设角色扮演（后者请用 nuwa-skill）

## 核心方法论：RIA-TV++

一个五阶段 + 并行提取 + 三重验证 + 机器证据链 + darwin 兼容测试的流水线。方法论总览见 `methodology/00-overview.md`，机器质量门禁见 `methodology/08-machine-quality-gates.md`。

```text
阶段 0: Adler 整体理解       → BOOK_OVERVIEW.md
阶段 1: 5 个 agent 并行提取  → 候选方法论单元池
阶段 1.5: 三重验证筛选       → 通过的单元（用户轻确认）
阶段 2: RIA++ 构造 skill     → SKILL.md + skill.yaml + evidence.json
阶段 3: Zettelkasten 链接    → INDEX.md + GLOSSARY.md + sibling routing
阶段 4: 压力测试             → test-prompts.json + test-results.json
阶段 5: 质量门禁与交付       → DIGEST.md + 通过校验的安装包
```

## 何时调用此 skill

用户说类似：
- “帮我拆《穷查理宝典》”
- “把毛选蒸馏成 skill”
- “把这个 B 站视频 / 播客 / 课程蒸馏成 skill”
- “distill this book into skills: <path>”
- “我想把这本书的方法论做成可用的 skill”

不要因为用户只说“总结一下”“介绍一下”就启动本流程。

## 输入要求

在开始前**必须**从用户处确认：

1. **内容文本来源**：PDF / EPUB / TXT / 字幕文件 / 转写稿路径，或可访问的纯文本。**不要**在没有文本的情况下“凭记忆”蒸馏——宁可停下来问用户要。
2. **内容元信息**：书籍是“书名 + 作者 + 出版年”；视频 / 播客 / 课程是“标题 + 作者（UP 主 / 主播 / 讲者）+ 发布时间”。
3. **使用目标**：生成的 skills 主要用于哪些真实工作场景。这个信息用于候选排序，不得用来曲解原文。
4. **是否首次试点**：第一次使用时，优先只蒸馏 1 份内容验证流程再批量。

**非书籍内容的字段映射**：`source_chapter` 等“章节”字段，对视频填时间戳或分 P，对播客填集数，对课程填讲次；必须保证可回查。

## 双轨产物

每个项目同时维护两类产物：

- **人类可读层**：用于阅读、讨论和人工审核的 Markdown。
- **机器可验证层**：用于 Schema、证据引用、测试执行和 CI 门禁的 YAML / JSON。

Markdown 不是机器状态的唯一来源；机器文件也不能代替给用户看的解释。

## 输出结构

```text
books/<book-slug>/
├── pipeline-state.json         # 机器状态源：阶段、产物、模型运行、下一步
├── PIPELINE_STATE.md           # 面向人的状态摘要（与 JSON 同步）
├── BOOK_OVERVIEW.md            # 阶段 0：主旨 / 骨架 / 术语 / 批判
├── verified.md                 # 阶段 1.5：通过验证的单元 + 判定理由
├── INDEX.md                    # 阶段 3：skill 总览 + 引用图
├── GLOSSARY.md                 # 阶段 3：全书共享术语词典
├── DIGEST.md                   # 阶段 5：面向读者的精华长文
├── candidates/                 # 阶段 1：原始候选池（审计用）
├── rejected/                   # 阶段 1.5：淘汰单元 + 原因（审计用）
├── <skill-slug-1>/
│   ├── SKILL.md                # 人类可读说明
│   ├── skill.yaml              # 机器可读路由、流程、边界、验证状态
│   ├── evidence.json           # 来源定位、claim_type、文本哈希
│   ├── test-prompts.json       # darwin 兼容测试输入
│   ├── test-results.json       # 实际执行结果；禁止伪造
│   └── test-results.md         # 面向人的失败分析与修订说明
└── <skill-slug-2>/
    └── ...
```

## 执行流程（严格按顺序）

### 断点续跑

开始前先检查 `books/<slug>/pipeline-state.json`；存在则按机器状态续跑，不要从头重来。`PIPELINE_STATE.md` 是人类摘要，必须与 JSON 保持一致。

每完成一个阶段：

1. 更新已完成产物、各 skill 状态和下一步；
2. 记录模型 / prompt 版本（环境支持时）；
3. 校验 `pipeline-state.json`：

```bash
python scripts/quality_gate.py --state books/<slug>/pipeline-state.json
```

### 阶段 0 — 整体理解

1. 读取用户提供的完整内容；大文件按可回溯位置分块。
2. 执行 `methodology/01-stage0-adler.md` 中的 Adler 四步（结构 / 解释 / 批判 / 应用）。
3. 按 `templates/BOOK_OVERVIEW.md.template` 生成 `BOOK_OVERVIEW.md`。
4. 向用户确认：“骨架理解是否正确？希望重点突出哪些方向？”确认后再进入阶段 1。

### 阶段 1 — 5 个 sub-agent 并行提取

**并行**启动 5 个独立提取任务；不支持并行时按同样 prompt 串行执行，产出格式不变。

| sub-agent | prompt | 产出 |
|---|---|---|
| 框架提取器 | `extractors/framework-extractor.md` | 决策框架 / 思维模型 |
| 原则提取器 | `extractors/principle-extractor.md` | 原则 / 清单 / 规则 |
| 案例提取器 | `extractors/case-extractor.md` | 作者在原内容中使用过的实例 |
| 反例提取器 | `extractors/counter-example-extractor.md` | 失败模式 / 不适用场景 |
| 术语提取器 | `extractors/glossary-extractor.md` | 关键概念词典 |

每个任务独立读取、独立提取、独立输出到 `books/<slug>/candidates/<type>.md`。长内容按 `methodology/02-stage1-parallel-extract.md` 分块，并保留页码、时间戳、行号或段落位置。

### 阶段 1.5 — 三重验证筛选

读取 `methodology/03-stage1.5-triple-verify.md`，对每个候选执行：

- **V1 跨域**：原内容中至少有 2 个相对独立的位置提供支持？
- **V2 预测力**：能否用它处理原内容未直接回答的新问题？
- **V3 独特性**：是否超出泛泛常识，值得单独调用？

通过的写入 `verified.md`；不通过的写入 `rejected/` 并附原因。

**用户轻确认**：展示“通过的 N 个候选 + 淘汰的 M 个”，询问是否捞回、合并或砍掉。确认后再进入阶段 2。

### 阶段 2 — RIA++ 构造 skill 与证据链

对每个通过单元：

1. 按 `templates/SKILL.md.template` 生成人类可读 `SKILL.md`：
   - **R**：原文引用与精确位置；
   - **I**：方法论骨架；
   - **A1**：原内容中的应用；
   - **A2**：未来触发场景与相邻 skill 区分；
   - **E**：可执行步骤、完成标准、判停条件；
   - **B**：不适用场景、失败模式、作者盲点。
2. 按 `templates/skill.yaml.template` 生成 `skill.yaml`，把路由、流程、边界和输出合同变成机器字段。
3. 按 `templates/evidence.json.template` 生成 `evidence.json`。每项必须有稳定 `evidence_id`、位置和 `text_hash`。
4. 每条主张明确标记：
   - `direct_quote`
   - `author_paraphrase`
   - `inferred_method`
   - `model_critique`
   - `external_fact`
5. `SKILL.md` 与 `skill.yaml` 必须引用相同的证据语义；不得把模型推断伪装为作者原意。
6. 阶段 2 初始状态必须是：
   - `status: draft`
   - `verification.hard_gate_passed: false`

机器格式细则见 `methodology/08-machine-quality-gates.md`。

### 阶段 3 — Zettelkasten 链接

按 `methodology/05-stage3-zettelkasten.md`：

1. 建立 skill 之间的依赖、对比和组合关系；
2. 更新每个 `SKILL.md` 的“相关 skills”；
3. 更新 `skill.yaml.routing.sibling_priority`，明确相邻 skill 的优先条件；
4. 生成 `INDEX.md` 和共享 `GLOSSARY.md`。

### 阶段 4 — 压力测试与机器判卷

按 `methodology/06-stage4-pressure-test.md`：

1. 按 `templates/test-prompts.json.template` 生成 `test-prompts.json`。
2. 至少包含：
   - 3 条 `should_trigger`
   - 2 条 `should_not_trigger`
   - 1 条 `edge_case`
   - 负例中至少 1 条显式填写 `sibling_skill`
3. 优先用未参与蒸馏的独立 agent 盲测；不给它看 `type`、预期答案和判分标准。
4. 把**实际执行结果**写入 `test-results.json`，并用 `test-results.md` 解释失败案例和修订过程。
5. 禁止仅生成测试题却声称“经过测试”；禁止把“待测”写成通过。
6. 负例零容错；总通过率必须达到 `minimum_pass_rate`。
7. 通过后更新：
   - `verification.hard_gate_passed: true`
   - `status: tested`
8. 对每个 bundle 运行：

```bash
python scripts/quality_gate.py books/<slug>/<skill-slug>
```

失败必须回炉阶段 2 / 3 / 4，不得只修改测试来迎合现有结果。

### 阶段 5 — 质量门禁与交付

1. 按 `templates/DIGEST.md.template` 生成 `DIGEST.md`。
2. 执行全项目校验：

```bash
python scripts/quality_gate.py --all
```

3. 只有通过 Schema、证据引用、哈希、测试执行和负例门槛的 skill 才能安装。
4. 询问安装位置（用户级或项目级），复制或 symlink **完整 bundle**；宿主只需要 `SKILL.md` 时，也要保留机器文件作为审计产物。
5. 发布后把 `status` 更新为 `published`，并记录版本变化。

## 旧产物迁移

对只有 `SKILL.md` / `test-prompts.json` 的旧 skill，可先预览迁移：

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill
```

确认后写入保守脚手架：

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill --write
```

迁移器会生成 `skill.yaml`、`evidence.json` 和审计报告，但会保持 `hard_gate_passed: false`，且**不会伪造 `test-results.json`**。详细步骤见 `docs/LEGACY-MIGRATION.md`。

## 质量红线（违反则阻止交付）

1. 每个 skill 必须通过 V1 / V2 / V3，并在机器文件中保留验证状态。
2. 每个 skill 必须同时有 `SKILL.md`、`skill.yaml`、`evidence.json`、`test-prompts.json`、`test-results.json`。
3. 原文引用必须可定位，并满足引用长度限制。
4. 作者观点、模型推断、模型批判和外部事实必须分层标记。
5. `description` 和 `routing` 必须明确何时调用、何时不调用；不能只是“一个关于 X 的 skill”。
6. 至少 3 正例、2 负例、1 边界例；必须有兄弟 skill 混淆负例。
7. 所有负例必须通过，汇总数字必须与逐条结果一致。
8. 没有真实 `test-results.json` 的 skill 不得标注“已测试”。
9. `python scripts/quality_gate.py --all` 未通过时不得安装或发布。

## 与 nuwa-skill / darwin-skill 的生态定位

- **nuwa-skill**：蒸馏人（思维方式 / 表达 DNA）
- **cangjie-skill**：蒸馏长内容（方法论 / 框架 / 原则）
- **darwin-skill**：进化任意 skill

`test-prompts.json` 保持 darwin 兼容；`test-results.json` 与证据链补充了发布前的可审计门禁。

## 调用惯例

- **永远先试点 1 份内容**——除非用户明确要求批量。
- **阶段之间主动汇报进度**——不要静默跑完再一次性倾倒结果。
- **不凭记忆拆书**——没文本就停下来索取来源。
- **保留审计轨迹**——`candidates/`、`rejected/`、证据和测试结果都要留。
- **机器状态优先续跑**——每完成一个阶段更新 `pipeline-state.json`。
- **不把生成等同于验证**——写出测试题不代表测试已执行。

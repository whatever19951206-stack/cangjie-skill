# P2：项目运行时与成本控制

P2 把 P0 的质量门禁和 P1 的效果评测包装成可重复运行的项目工作流。CLI 不内置任何模型供应商；它生成确定性的任务计划、缓存键和状态文件，交给当前 Agent 环境执行。

## 三种模式

| 模式 | 用途 | 提取器 | 最大候选 | 最大 Skills | 独立评审 | 隐藏评测 | 外部核验 |
|---|---|---:|---:|---:|---:|---:|---:|
| `scan` | 快速判断是否值得蒸馏 | 框架、原则 | 12 | 3 | 否 | 否 | 否 |
| `standard` | 个人长期使用的默认模式 | 全部 5 类 | 30 | 5 | 是 | 否 | 否 |
| `audit` | 高风险或组织级使用 | 全部 5 类 | 60 | 10 | 是 | 是 | 是 |

查看机器中的实时配置：

```bash
python scripts/cangjie.py profile
python scripts/cangjie.py profile audit
```

## 初始化项目

```bash
python scripts/cangjie.py init books/my-source \
  --project-id my-source \
  --mode standard \
  --source-id my-source-v1 \
  --source-title "My Source" \
  --source-type book \
  --source-file /path/to/source.txt \
  --goal "用于管理决策和风险分析"
```

CLI 会生成：

```text
books/my-source/
├── project.yaml
├── pipeline-state.json
├── source/
├── work/
├── skills/
├── evals/
├── reports/
└── dist/
```

`source_hash` 在初始化时固定。材料内容改变后，旧 chunk、任务和缓存不能被静默复用。

## 稳定分块

```bash
python scripts/cangjie.py chunk books/my-source \
  --source-file /path/to/source.txt \
  --max-chars 12000
```

输出 `source/chunks.jsonl`，每块包含：

- 稳定 `chunk_id`
- `content_hash`
- 原文件字符位置 `source_ref`

这一步不把整份原文复制到计划文件中，只保留定位与哈希。

## Map-Reduce 计划

```bash
python scripts/cangjie.py plan books/my-source \
  --prompt-version extractors-v1 \
  --model-id model-x
```

任务结构：

```text
chunk × extractor → map tasks
每个 extractor 的 map 输出 → reduce task
所有 reduce 输出 → merge.candidates
合并候选 → verify.candidates
```

与“五个 Agent 分别阅读全文”相比，Map 阶段只处理局部 chunk；Reduce 阶段只读取结构化局部结果。

计划输出：

```text
work/extraction-plan.json
```

## 缓存与断点续跑

每个任务的 `cache_key` 由下列信息确定：

- source hash
- chunk hash
- extractor
- prompt version
- model ID
- 上游 dependency cache keys
- 模式和候选限制（适用时）

记录任务成功：

```bash
python scripts/cangjie.py record-task books/my-source \
  --task-id map.framework.chunk-0001 \
  --status success \
  --artifact work/map/framework/chunk-0001.json \
  --model-id model-x
```

重新执行 `plan` 时，只有 cache key 相同且 artifact 仍存在的任务会被复用。修改原文、prompt 或模型后，受影响任务自动回到 `pending`。

查看进度：

```bash
python scripts/cangjie.py status books/my-source
```

## 项目校验

```bash
python scripts/cangjie.py validate books/my-source
```

校验范围：

- `project.yaml`
- `pipeline-state.json`
- `extraction-plan.json`
- dependency 完整性和循环
- `skills/*` 下的 P0 bundle

## 导出

只有通过 P0 quality gate 的 bundle 默认允许导出：

```bash
python scripts/cangjie.py export books/my-source/skills/failure-preflight \
  --target generic \
  --output books/my-source/dist
```

支持目标：

- `generic`：可移植完整 bundle
- `claude`：暂存到 `.claude/skills/<name>/`
- `cursor`：暂存到 `.cursor/skills/<name>/`
- `codex`：暂存到中性的 `codex-skills/<name>/`，并生成 `INSTALL-CODEX.md`

Codex 导出器故意不假设产品专用的全局安装路径。它只生成可审查的仓库级暂存包，之后由项目的 agent instructions 引用。

每个导出包包含 `export-manifest.json` 和文件哈希。

评审 draft 时可以显式绕过发布门禁：

```bash
python scripts/cangjie.py export path/to/draft \
  --target generic \
  --output /tmp/review \
  --allow-draft
```

此时 manifest 会记录 `validated: false`，不得当作正式发布包。

## 设计边界

CLI 负责：

- 项目结构
- 模式配置
- 分块
- 任务编排
- 状态和缓存
- 校验
- 导出

CLI 不负责：

- 选择具体模型服务
- 自动上传原文
- 替代独立评审
- 伪造模型运行结果
- 绕过 P0/P1 发布标准

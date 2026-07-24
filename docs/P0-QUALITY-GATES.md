# P0：机器可验证的质量门禁

这一层不替代现有 RIA-TV++ 流程，而是把原先依赖 Agent 自觉遵守的质量红线变成可执行检查。

## 新增的机器产物

每个准备交付的 Skill 目录应同时包含：

```text
<skill-dir>/
├── SKILL.md             # 人类与宿主可读版本，保留现有格式
├── skill.yaml           # 机器可读的路由、执行、边界和验证信息
├── evidence.json        # 稳定证据 ID、定位信息、声明类型和文本哈希
├── test-prompts.json    # Darwin 兼容测试输入
└── test-results.json    # 实际执行结果，不再只写“待测”
```

项目级断点状态可逐步从 `PIPELINE_STATE.md` 迁移为 `pipeline-state.json`。迁移期允许两者并存。

## 声明类型

`evidence.json` 强制把内容分成：

- `direct_quote`：原文直接引用；
- `author_paraphrase`：对作者观点的忠实转述；
- `inferred_method`：模型基于多个证据归纳的方法；
- `model_critique`：模型对作者局限的批判；
- `external_fact`：来自原材料之外、需要独立来源的事实。

这可以避免模型推断或批判被包装成“作者原意”。

## 自动检查

```bash
python -m pip install -r requirements-quality.txt
python scripts/quality_gate.py examples/quality-gate-sample
python scripts/quality_gate.py --all
python -m unittest discover -s tests -p 'test_*.py'
```

质量门禁检查：

1. YAML/JSON 是否符合 Schema；
2. 是否残留模板占位符；
3. `evidence_refs` 是否真实存在；
4. 证据 ID 是否重复；
5. 证据文本 SHA-256 是否匹配；
6. 来源 ID 和来源哈希是否跨文件一致；
7. 测试是否覆盖至少 3 个正例、2 个负例和 1 个边界例；
8. 是否包含至少一个兄弟 Skill 混淆负例；
9. 每个测试是否真的存在执行结果；
10. 汇总数字是否和逐条结果一致；
11. 所有负例是否通过；
12. 通过率是否达到 `minimum_pass_rate`；
13. `tested` / `published` Skill 是否已通过硬门禁。

## 兼容策略

P0 是增量改造：

- 不删除现有 `SKILL.md`、`test-results.md` 和 RIA++ 文档；
- 新生成的 Skill 同时写人类格式和机器格式；
- 旧产物可继续存在，但只有具备完整机器产物的目录才进入自动发布门禁；
- P1 再加入基线模型与安装 Skill 后模型的 A/B 效果评测。

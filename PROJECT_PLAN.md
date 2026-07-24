# Cangjie Skill 工程化路线

## P0：可信与可审计

- [x] `skill.yaml` Schema
- [x] `evidence.json` Schema
- [x] 测试输入与结果 Schema
- [x] 流水线状态 Schema
- [x] 跨文件证据引用和哈希校验
- [x] 测试是否真正执行的校验
- [x] 负例零容错和兄弟 Skill 混淆门禁
- [x] 示例 bundle
- [x] 单元测试
- [x] GitHub Actions
- [x] 将机器产物写入主 `SKILL.md` 的正式执行步骤
- [x] 为旧产物提供保守迁移脚本
- [x] 对齐人类模板、测试模板与机器门禁

## P1：证明 Skill 确实带来提升（本分支）

- [x] 路由、执行、忠实度三套评测分离
- [x] 同一个模型 baseline 与加载 Skill 后的公平 A/B 合同
- [x] hidden case 标记与报告脱敏（正式隐藏题需放在私有存储）
- [x] 兄弟 Skill 混淆矩阵
- [x] `uplift` 指标自动计算
- [x] case-level regression 门禁
- [x] JSONL Schema、示例、模板和单元测试
- [x] CI 中运行示例 uplift gate

## P2：降低使用与运行成本

- [ ] `scan / standard / audit` 三档模式
- [ ] Map-Reduce 提取，避免五个 Agent 重复阅读全文
- [ ] 增量缓存和状态恢复
- [ ] CLI 与 Claude、Cursor、Codex、Generic 导出器

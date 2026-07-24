from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate_legacy_skill.py"
spec = importlib.util.spec_from_file_location("migrate_legacy_skill", SCRIPT_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)


LEGACY_SKILL = """---
name: inversion-thinking
description: 当用户在高代价决策中只看到收益、需要从失败路径反推预防动作时调用；不适用于纯事实查询和低成本日常选择。
source_book: 《测试之书》 测试作者
source_chapter: 第三章，第 42 页
---

# 逆向思维

## R — 原文 (Reading)

> 先研究怎样会失败，再避免走上这些路径。
>
> — 测试作者，第三章

---

## I — 方法论骨架 (Interpretation)

从不可接受结果反推失败路径。

---

## A2 — 触发场景 (Future Trigger) ★

### 用户会在什么情境下需要这个 skill?

1. 用户准备做高代价且难撤销的决策
2. 用户主要罗列收益而没有检查失败条件

### 语言信号

- "哪里可能出事"
- "最坏会怎样"

### 与相邻 skill 的区分

- 与 checklist-review 的区别: 本方法先寻找失败路径。

---

## E — 可执行步骤 (Execution)

1. **定义失败结果**
   - 完成标准: 至少写出一个可观察的失败状态

2. **列出失败路径**
   - 完成标准: 覆盖人员、资源、时间和外部环境
   - 判停条件: 没有关键上下文时先询问用户

---

## B — 边界 (Boundary) ★

### 不要在以下情况使用此 skill

- 纯事实查询
- 低成本且容易撤销的日常选择

### 作者在书中警告的失败模式

- 不要把悲观想象当成证据。
"""

LEGACY_TESTS = {
    "skill": "old-name",
    "version": "0.1.0",
    "test_cases": [
        {
            "id": "positive-1",
            "category": "should_trigger",
            "prompt": "准备签十年合同，帮我找致命风险",
            "expected_behavior": "调用 inversion-thinking",
        },
        {
            "id": "negative-1",
            "category": "should_not_trigger",
            "prompt": "这个 API 参数是什么",
            "expected_behavior": "不调用",
        },
    ],
    "minimum_pass_rate": 0.8,
}


class MigrationTests(unittest.TestCase):
    def make_legacy_dir(self, include_quote: bool = True) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name) / "inversion-thinking"
        root.mkdir()
        content = LEGACY_SKILL if include_quote else LEGACY_SKILL.replace(
            "> 先研究怎样会失败，再避免走上这些路径。\n>\n> — 测试作者，第三章",
            "本节没有引用。",
        )
        (root / "SKILL.md").write_text(content, encoding="utf-8")
        (root / "test-prompts.json").write_text(
            json.dumps(LEGACY_TESTS, ensure_ascii=False), encoding="utf-8"
        )
        return root

    def test_builds_conservative_scaffold(self) -> None:
        root = self.make_legacy_dir()
        result = migration.build_migration(root)

        self.assertEqual(result.skill["name"], "inversion-thinking")
        self.assertEqual(result.skill["status"], "draft")
        self.assertFalse(result.skill["verification"]["hard_gate_passed"])
        self.assertEqual(result.skill["routing"]["include"][0], "用户准备做高代价且难撤销的决策")
        self.assertEqual(result.skill["workflow"][1]["stop_if"], "没有关键上下文时先询问用户")

        item = result.evidence["evidence"][0]
        self.assertEqual(item["claim_type"], "direct_quote")
        self.assertEqual(item["text_hash"], migration.sha256_text(item["text"]))
        self.assertEqual(result.test_prompts["skill"], "inversion-thinking")
        self.assertEqual(result.test_prompts["test_cases"][0]["type"], "should_trigger")
        self.assertIn("test-results.json was intentionally not generated", " ".join(result.report["warnings"]))

    def test_write_does_not_fabricate_test_results(self) -> None:
        root = self.make_legacy_dir()
        result = migration.build_migration(root)
        migration.write_result(root, result, force=False)

        self.assertTrue((root / "skill.yaml").is_file())
        self.assertTrue((root / "evidence.json").is_file())
        self.assertTrue((root / "migration-report.json").is_file())
        self.assertTrue((root / "test-prompts.migrated.json").is_file())
        self.assertFalse((root / "test-results.json").exists())

        skill = yaml.safe_load((root / "skill.yaml").read_text(encoding="utf-8"))
        self.assertEqual(skill["status"], "draft")

    def test_refuses_to_overwrite_without_force(self) -> None:
        root = self.make_legacy_dir()
        result = migration.build_migration(root)
        migration.write_result(root, result, force=False)
        with self.assertRaises(migration.MigrationError):
            migration.write_result(root, result, force=False)
        migration.write_result(root, result, force=True)

    def test_requires_source_quote(self) -> None:
        root = self.make_legacy_dir(include_quote=False)
        with self.assertRaises(migration.MigrationError):
            migration.build_migration(root)


if __name__ == "__main__":
    unittest.main()

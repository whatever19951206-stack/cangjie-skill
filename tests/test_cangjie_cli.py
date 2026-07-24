from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cangjie.py"
spec = importlib.util.spec_from_file_location("cangjie_cli", SCRIPT_PATH)
assert spec and spec.loader
cangjie = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cangjie
spec.loader.exec_module(cangjie)

ROOT = Path(__file__).resolve().parents[1]
VALID_BUNDLE = ROOT / "examples" / "quality-gate-sample"


class CangjieCliTests(unittest.TestCase):
    def make_workspace(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source.txt"
        source.write_text(("第一段方法说明。\n\n" * 40) + ("第二段案例说明。\n\n" * 40), encoding="utf-8")
        return root, source

    def init_project(self, root: Path, source: Path, mode: str = "standard", project_name: str = "demo") -> Path:
        project = root / project_name
        code = cangjie.main(
            [
                "init",
                str(project),
                "--project-id",
                project_name,
                "--mode",
                mode,
                "--source-id",
                f"{project_name}-source",
                "--source-title",
                "测试材料",
                "--source-type",
                "document-set",
                "--source-file",
                str(source),
                "--goal",
                "测试项目运行流程",
            ]
        )
        self.assertEqual(code, 0)
        return project

    def test_mode_profiles_are_materially_different(self) -> None:
        self.assertEqual(cangjie.MODE_PROFILES["scan"]["extractors"], ["framework", "principle"])
        self.assertFalse(cangjie.MODE_PROFILES["scan"]["independent_evaluator"])
        self.assertTrue(cangjie.MODE_PROFILES["audit"]["hidden_evaluation"])
        self.assertTrue(cangjie.MODE_PROFILES["audit"]["external_validation"])
        self.assertLess(cangjie.MODE_PROFILES["scan"]["max_skills"], cangjie.MODE_PROFILES["audit"]["max_skills"])

    def test_init_chunk_plan_and_incremental_cache(self) -> None:
        root, source = self.make_workspace()
        project = self.init_project(root, source)
        self.assertEqual(cangjie.main(["chunk", str(project), "--source-file", str(source), "--max-chars", "220"]), 0)
        self.assertEqual(
            cangjie.main(
                [
                    "plan",
                    str(project),
                    "--prompt-version",
                    "extractors-v1",
                    "--model-id",
                    "model-a",
                ]
            ),
            0,
        )
        plan_path = project / "work" / "extraction-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        chunk_count = len(plan["chunks"])
        self.assertEqual(len(plan["tasks"]), chunk_count * 5 + 7)

        first = plan["tasks"][0]
        artifact = project / first["artifact"]
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")
        self.assertEqual(
            cangjie.main(
                [
                    "record-task",
                    str(project),
                    "--task-id",
                    first["task_id"],
                    "--status",
                    "success",
                    "--artifact",
                    first["artifact"],
                    "--model-id",
                    "model-a",
                ]
            ),
            0,
        )

        self.assertEqual(
            cangjie.main(
                [
                    "plan",
                    str(project),
                    "--prompt-version",
                    "extractors-v1",
                    "--model-id",
                    "model-a",
                ]
            ),
            0,
        )
        replanned = json.loads(plan_path.read_text(encoding="utf-8"))
        cached = next(task for task in replanned["tasks"] if task["task_id"] == first["task_id"])
        self.assertEqual(cached["status"], "success")

        self.assertEqual(
            cangjie.main(
                [
                    "plan",
                    str(project),
                    "--prompt-version",
                    "extractors-v2",
                    "--model-id",
                    "model-a",
                ]
            ),
            0,
        )
        invalidated = json.loads(plan_path.read_text(encoding="utf-8"))
        changed = next(task for task in invalidated["tasks"] if task["task_id"] == first["task_id"])
        self.assertEqual(changed["status"], "pending")
        self.assertNotEqual(changed["cache_key"], first["cache_key"])

    def test_scan_plan_uses_fewer_map_tasks(self) -> None:
        root, source = self.make_workspace()
        scan_project = self.init_project(root, source, mode="scan", project_name="scan-demo")
        audit_project = self.init_project(root, source, mode="audit", project_name="audit-demo")
        for project in (scan_project, audit_project):
            self.assertEqual(cangjie.main(["chunk", str(project), "--source-file", str(source), "--max-chars", "5000"]), 0)
            self.assertEqual(cangjie.main(["plan", str(project)]), 0)
        scan_plan = json.loads((scan_project / "work" / "extraction-plan.json").read_text(encoding="utf-8"))
        audit_plan = json.loads((audit_project / "work" / "extraction-plan.json").read_text(encoding="utf-8"))
        scan_maps = [task for task in scan_plan["tasks"] if task["stage"] == "map"]
        audit_maps = [task for task in audit_plan["tasks"] if task["stage"] == "map"]
        self.assertEqual(len(scan_maps) * 5, len(audit_maps) * 2)

    def test_source_hash_change_blocks_chunking(self) -> None:
        root, source = self.make_workspace()
        project = self.init_project(root, source)
        source.write_text(source.read_text(encoding="utf-8") + "内容发生变化。", encoding="utf-8")
        self.assertEqual(cangjie.main(["chunk", str(project), "--source-file", str(source)]), 2)

    def test_validated_exports_for_all_targets(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for target in ("generic", "claude", "cursor", "codex"):
            output = root / target
            self.assertEqual(
                cangjie.main(
                    [
                        "export",
                        str(VALID_BUNDLE),
                        "--target",
                        target,
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
        self.assertTrue((root / "generic" / "failure-preflight" / "export-manifest.json").is_file())
        self.assertTrue((root / "claude" / ".claude" / "skills" / "failure-preflight" / "SKILL.md").is_file() is False)
        self.assertTrue((root / "claude" / ".claude" / "skills" / "failure-preflight" / "skill.yaml").is_file())
        self.assertTrue((root / "cursor" / ".cursor" / "skills" / "failure-preflight" / "evidence.json").is_file())
        self.assertTrue((root / "codex" / "codex-skills" / "failure-preflight" / "test-results.json").is_file())
        self.assertTrue((root / "codex" / "INSTALL-CODEX.md").is_file())

    def test_draft_export_requires_explicit_override(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        draft = root / "draft-bundle"
        shutil.copytree(VALID_BUNDLE, draft)
        skill_path = draft / "skill.yaml"
        skill = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
        skill["status"] = "draft"
        skill["verification"]["hard_gate_passed"] = False
        skill_path.write_text(yaml.safe_dump(skill, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.assertEqual(
            cangjie.main(["export", str(draft), "--target", "generic", "--output", str(root / "out")]),
            2,
        )
        self.assertEqual(
            cangjie.main(
                [
                    "export",
                    str(draft),
                    "--target",
                    "generic",
                    "--output",
                    str(root / "review"),
                    "--allow-draft",
                ]
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()

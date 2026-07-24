from __future__ import annotations

import contextlib
import importlib.util
import io
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
    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cangjie.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_cli_ok(self, arguments: list[str]) -> dict:
        code, stdout, stderr = self.run_cli(arguments)
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)

    def make_workspace(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source.txt"
        source.write_text(("第一段方法说明。\n\n" * 40) + ("第二段案例说明。\n\n" * 40), encoding="utf-8")
        return root, source

    def init_project(self, root: Path, source: Path, mode: str = "standard", project_name: str = "demo") -> Path:
        project = root / project_name
        self.assert_cli_ok(
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
        self.assert_cli_ok(["chunk", str(project), "--source-file", str(source), "--max-chars", "220"])
        self.assert_cli_ok(
            ["plan", str(project), "--prompt-version", "extractors-v1", "--model-id", "model-a"]
        )
        plan_path = project / "work" / "extraction-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        chunk_count = len(plan["chunks"])
        self.assertEqual(len(plan["tasks"]), chunk_count * 5 + 7)

        first = plan["tasks"][0]
        artifact = project / first["artifact"]
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")
        self.assert_cli_ok(
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
        )

        self.assert_cli_ok(
            ["plan", str(project), "--prompt-version", "extractors-v1", "--model-id", "model-a"]
        )
        replanned = json.loads(plan_path.read_text(encoding="utf-8"))
        cached = next(task for task in replanned["tasks"] if task["task_id"] == first["task_id"])
        self.assertEqual(cached["status"], "success")

        self.assert_cli_ok(
            ["plan", str(project), "--prompt-version", "extractors-v2", "--model-id", "model-a"]
        )
        invalidated = json.loads(plan_path.read_text(encoding="utf-8"))
        changed = next(task for task in invalidated["tasks"] if task["task_id"] == first["task_id"])
        self.assertEqual(changed["status"], "pending")
        self.assertNotEqual(changed["cache_key"], first["cache_key"])
        state = json.loads((project / "pipeline-state.json").read_text(encoding="utf-8"))
        self.assertIn(first["task_id"], state["pending_tasks"])
        self.assertNotIn(first["task_id"], state["completed_tasks"])
        self.assertFalse(set(state["completed_tasks"]) & set(state["pending_tasks"]))

    def test_scan_plan_uses_fewer_map_tasks(self) -> None:
        root, source = self.make_workspace()
        scan_project = self.init_project(root, source, mode="scan", project_name="scan-demo")
        audit_project = self.init_project(root, source, mode="audit", project_name="audit-demo")
        for project in (scan_project, audit_project):
            self.assert_cli_ok(["chunk", str(project), "--source-file", str(source), "--max-chars", "5000"])
            self.assert_cli_ok(["plan", str(project)])
        scan_plan = json.loads((scan_project / "work" / "extraction-plan.json").read_text(encoding="utf-8"))
        audit_plan = json.loads((audit_project / "work" / "extraction-plan.json").read_text(encoding="utf-8"))
        scan_maps = [task for task in scan_plan["tasks"] if task["stage"] == "map"]
        audit_maps = [task for task in audit_plan["tasks"] if task["stage"] == "map"]
        self.assertEqual(len(scan_maps) * 5, len(audit_maps) * 2)

    def test_source_hash_change_blocks_chunking(self) -> None:
        root, source = self.make_workspace()
        project = self.init_project(root, source)
        source.write_text(source.read_text(encoding="utf-8") + "内容发生变化。", encoding="utf-8")
        code, _, stderr = self.run_cli(["chunk", str(project), "--source-file", str(source)])
        self.assertEqual(code, 2)
        self.assertIn("source hash mismatch", stderr)

    def test_validated_exports_for_all_targets(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for target in ("generic", "claude", "cursor", "codex"):
            self.assert_cli_ok(
                ["export", str(VALID_BUNDLE), "--target", target, "--output", str(root / target)]
            )
        self.assertTrue((root / "generic" / "failure-preflight" / "export-manifest.json").is_file())
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

        code, _, stderr = self.run_cli(
            ["export", str(draft), "--target", "generic", "--output", str(root / "out")]
        )
        self.assertEqual(code, 2)
        self.assertIn("bundle failed quality gate", stderr)
        manifest = self.assert_cli_ok(
            [
                "export",
                str(draft),
                "--target",
                "generic",
                "--output",
                str(root / "review"),
                "--allow-draft",
            ]
        )
        self.assertFalse(manifest["validated"])


if __name__ == "__main__":
    unittest.main()

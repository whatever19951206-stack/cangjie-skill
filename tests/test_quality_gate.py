from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("quality_gate", ROOT / "scripts" / "quality_gate.py")
quality_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = quality_gate
assert SPEC.loader is not None
SPEC.loader.exec_module(quality_gate)


class QualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bundle = Path(self.temp_dir.name) / "bundle"
        shutil.copytree(ROOT / "examples" / "quality-gate-sample", self.bundle)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def read_yaml(self, name: str):
        return yaml.safe_load((self.bundle / name).read_text(encoding="utf-8"))

    def write_yaml(self, name: str, value) -> None:
        (self.bundle / name).write_text(
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def read_json(self, name: str):
        return json.loads((self.bundle / name).read_text(encoding="utf-8"))

    def write_json(self, name: str, value) -> None:
        (self.bundle / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def messages(self):
        return "\n".join(issue.message for issue in quality_gate.validate_bundle(self.bundle))

    def test_valid_bundle_passes(self):
        self.assertEqual([], quality_gate.validate_bundle(self.bundle))

    def test_unknown_evidence_reference_fails(self):
        skill = self.read_yaml("skill.yaml")
        skill["evidence_refs"].append("ev_missing")
        self.write_yaml("skill.yaml", skill)
        self.assertIn("unknown evidence_refs", self.messages())

    def test_evidence_hash_mismatch_fails(self):
        evidence = self.read_json("evidence.json")
        evidence["evidence"][0]["text"] += "已被篡改"
        self.write_json("evidence.json", evidence)
        self.assertIn("text_hash mismatch", self.messages())

    def test_missing_executed_result_fails(self):
        results = self.read_json("test-results.json")
        results["tests"].pop()
        results["summary"] = {
            "total": 5,
            "passed": 5,
            "failed": 0,
            "pass_rate": 1.0,
            "all_negative_passed": True,
            "status": "passed",
        }
        self.write_json("test-results.json", results)
        self.assertIn("missing executed results", self.messages())

    def test_failed_negative_case_blocks_release(self):
        results = self.read_json("test-results.json")
        target = next(case for case in results["tests"] if case["type"] == "should_not_trigger")
        target["passed"] = False
        results["summary"] = {
            "total": 6,
            "passed": 5,
            "failed": 1,
            "pass_rate": 5 / 6,
            "all_negative_passed": False,
            "status": "failed",
        }
        self.write_json("test-results.json", results)
        messages = self.messages()
        self.assertIn("quality threshold failed", messages)
        self.assertIn("tested/published skill must have passing results", messages)

    def test_unresolved_placeholder_fails(self):
        skill = self.read_yaml("skill.yaml")
        skill["description"] = "{{待填写的触发条件}}，这一段故意保留模板占位符。"
        self.write_yaml("skill.yaml", skill)
        self.assertIn("unresolved placeholder marker", self.messages())


if __name__ == "__main__":
    unittest.main()

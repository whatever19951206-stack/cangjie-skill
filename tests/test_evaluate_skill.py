from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_skill.py"
spec = importlib.util.spec_from_file_location("evaluate_skill", SCRIPT_PATH)
assert spec and spec.loader
evaluation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluation
spec.loader.exec_module(evaluation)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CASES = ROOT / "examples" / "evaluation-harness" / "cases.jsonl"
EXAMPLE_RESULTS = ROOT / "examples" / "evaluation-harness" / "results.jsonl"


class EvaluationHarnessTests(unittest.TestCase):
    def temporary_jsonl(self, records: list[dict]) -> Path:
        temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False)
        self.addCleanup(lambda: Path(temp.name).unlink(missing_ok=True))
        with temp:
            for record in records:
                temp.write(json.dumps(record, ensure_ascii=False) + "\n")
        return Path(temp.name)

    def load_records(self, path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_example_report_calculates_uplift_and_confusion(self) -> None:
        report = evaluation.evaluate(EXAMPLE_CASES, EXAMPLE_RESULTS)

        routing = report["suites"]["routing"]
        self.assertEqual(routing["baseline"]["accuracy"], 0.0)
        self.assertEqual(routing["skill"]["accuracy"], 1.0)
        self.assertEqual(
            routing["baseline"]["confusion_matrix"]["failure-preflight"]["checklist-review"],
            1,
        )
        self.assertEqual(routing["skill"]["confusion_matrix"]["__none__"]["__none__"], 1)

        execution = report["suites"]["execution"]
        self.assertEqual(execution["baseline_mean"], 0.41)
        self.assertEqual(execution["skill_mean"], 0.87)
        self.assertEqual(execution["uplift"], 0.46)

        faithfulness = report["suites"]["faithfulness"]
        self.assertEqual(faithfulness["baseline_mean"], 0.6)
        self.assertEqual(faithfulness["skill_mean"], 0.95)
        self.assertEqual(faithfulness["violations"]["baseline"]["unsupported_excess"], 1)

        self.assertEqual(report["overall"]["baseline_score"], 0.336667)
        self.assertEqual(report["overall"]["skill_score"], 0.94)
        self.assertEqual(report["overall"]["uplift"], 0.603333)
        self.assertEqual(report["overall"]["regressions"], [])

    def test_hidden_prompts_are_not_copied_to_report(self) -> None:
        report = evaluation.evaluate(EXAMPLE_CASES, EXAMPLE_RESULTS)
        rendered = json.dumps(report, ensure_ascii=False)
        hidden_prompts = [
            record["prompt"]
            for record in self.load_records(EXAMPLE_CASES)
            if record.get("visibility") == "hidden"
        ]
        for prompt in hidden_prompts:
            self.assertNotIn(prompt, rendered)
        self.assertFalse(report["privacy"]["prompts_included"])
        self.assertEqual(report["case_counts"]["hidden"], 2)

    def test_missing_paired_condition_fails(self) -> None:
        records = self.load_records(EXAMPLE_RESULTS)
        path = self.temporary_jsonl(records[:-1])
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate(EXAMPLE_CASES, path)

    def test_rubric_mismatch_fails(self) -> None:
        records = self.load_records(EXAMPLE_RESULTS)
        for record in records:
            if record["id"] == "execution-public-01" and record["condition"] == "skill":
                record["rubric_scores"].pop("reverse_actions")
        path = self.temporary_jsonl(records)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate(EXAMPLE_CASES, path)

    def test_model_mismatch_fails_fair_ab_requirement(self) -> None:
        records = self.load_records(EXAMPLE_RESULTS)
        records[1]["model_id"] = "different-model"
        path = self.temporary_jsonl(records)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate(EXAMPLE_CASES, path)

    def test_cli_uplift_gate(self) -> None:
        self.assertEqual(
            evaluation.main(
                [
                    "--cases",
                    str(EXAMPLE_CASES),
                    "--results",
                    str(EXAMPLE_RESULTS),
                    "--minimum-uplift",
                    "0.5",
                    "--fail-on-regression",
                ]
            ),
            0,
        )
        self.assertEqual(
            evaluation.main(
                [
                    "--cases",
                    str(EXAMPLE_CASES),
                    "--results",
                    str(EXAMPLE_RESULTS),
                    "--minimum-uplift",
                    "0.7",
                ]
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()

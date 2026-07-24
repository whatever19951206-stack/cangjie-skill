#!/usr/bin/env python3
"""Aggregate independently judged Cangjie Skill evaluations.

The harness deliberately does not ask a model to grade itself. It consumes:

- JSONL evaluation cases (routing, execution, faithfulness)
- JSONL evaluator annotations for two conditions: baseline and skill

It validates the contracts, compares the same model with and without the Skill,
computes routing confusion matrices, rubric scores, faithfulness violations, and
uplift, then writes a prompt-free report. Hidden prompts are never copied into
the report.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
CONDITIONS = ("baseline", "skill")
SUITES = ("routing", "execution", "faithfulness")
NONE_LABEL = "__none__"


class EvaluationError(RuntimeError):
    """Raised for malformed or incomplete evaluation input."""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationError(f"could not read {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise EvaluationError(f"{path}:{line_number}: each JSONL record must be an object")
        value["__line__"] = line_number
        records.append(value)
    if not records:
        raise EvaluationError(f"{path}: no records found")
    return records


def schema_validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        load_json(SCHEMA_DIR / schema_name),
        format_checker=FormatChecker(),
    )


def validate_records(
    records: Iterable[dict[str, Any]],
    schema_name: str,
    path: Path,
) -> None:
    validator = schema_validator(schema_name)
    messages: list[str] = []
    for record in records:
        line_number = record.get("__line__", "?")
        instance = {key: value for key, value in record.items() if key != "__line__"}
        for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            messages.append(f"{path}:{line_number}: {location}: {error.message}")
    if messages:
        raise EvaluationError("schema validation failed:\n- " + "\n- ".join(messages))


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "__line__"}


def validate_cases(cases: list[dict[str, Any]], path: Path) -> dict[str, dict[str, Any]]:
    validate_records(cases, "eval-case.schema.json", path)
    by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_id = case["id"]
        if case_id in by_id:
            raise EvaluationError(f"{path}: duplicate case id {case_id!r}")
        case.setdefault("visibility", "public")
        case.setdefault("tags", [])

        if case["suite"] == "execution":
            rubric = case["expected"]["rubric"]
            rubric_ids = [item["id"] for item in rubric]
            if len(set(rubric_ids)) != len(rubric_ids):
                raise EvaluationError(f"{path}: execution case {case_id!r} has duplicate rubric IDs")
            weight_sum = sum(float(item["weight"]) for item in rubric)
            if not math.isclose(weight_sum, 1.0, abs_tol=1e-9):
                raise EvaluationError(
                    f"{path}: execution case {case_id!r} rubric weights must sum to 1.0, got {weight_sum}"
                )

        by_id[case_id] = case
    return by_id


def validate_results(
    results: list[dict[str, Any]],
    path: Path,
    cases_by_id: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    validate_records(results, "eval-result.schema.json", path)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for result in results:
        case_id = result["id"]
        condition = result["condition"]
        key = (case_id, condition)
        if key in by_key:
            raise EvaluationError(f"{path}: duplicate result for {case_id!r} / {condition!r}")
        case = cases_by_id.get(case_id)
        if case is None:
            raise EvaluationError(f"{path}: result references unknown case {case_id!r}")
        if result["suite"] != case["suite"]:
            raise EvaluationError(
                f"{path}: result {case_id!r} suite {result['suite']!r} does not match case {case['suite']!r}"
            )

        if case["suite"] == "execution":
            expected_ids = {item["id"] for item in case["expected"]["rubric"]}
            actual_ids = set(result["rubric_scores"])
            if actual_ids != expected_ids:
                missing = sorted(expected_ids - actual_ids)
                extra = sorted(actual_ids - expected_ids)
                raise EvaluationError(
                    f"{path}: execution result {case_id!r}/{condition} rubric mismatch; "
                    f"missing={missing}, extra={extra}"
                )

        by_key[key] = result

    for case_id in cases_by_id:
        missing = [condition for condition in CONDITIONS if (case_id, condition) not in by_key]
        if missing:
            raise EvaluationError(f"{path}: case {case_id!r} is missing conditions {missing}")
        baseline_model = by_key[(case_id, "baseline")]["model_id"]
        skill_model = by_key[(case_id, "skill")]["model_id"]
        if baseline_model != skill_model:
            raise EvaluationError(
                f"{path}: case {case_id!r} compares different models: {baseline_model!r} vs {skill_model!r}"
            )
    return by_key


def label(value: str | None) -> str:
    return value if value is not None else NONE_LABEL


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def round_float(value: float) -> float:
    return round(float(value), 6)


def routing_metrics(
    cases: list[dict[str, Any]],
    results_by_key: dict[tuple[str, str], dict[str, Any]],
    condition: str,
) -> dict[str, Any]:
    pairs: list[tuple[str, str, str]] = []
    for case in cases:
        expected = label(case["expected"]["expected_skill"])
        predicted = label(results_by_key[(case["id"], condition)]["selected_skill"])
        pairs.append((case["id"], expected, predicted))

    labels = sorted({value for _, expected, predicted in pairs for value in (expected, predicted)})
    confusion = {expected: {predicted: 0 for predicted in labels} for expected in labels}
    for _, expected, predicted in pairs:
        confusion[expected][predicted] += 1

    per_label: dict[str, Any] = {}
    for current in labels:
        tp = sum(1 for _, expected, predicted in pairs if expected == current and predicted == current)
        fp = sum(1 for _, expected, predicted in pairs if expected != current and predicted == current)
        fn = sum(1 for _, expected, predicted in pairs if expected == current and predicted != current)
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1 = safe_div(2 * precision * recall, precision + recall)
        per_label[current] = {
            "precision": round_float(precision),
            "recall": round_float(recall),
            "f1": round_float(f1),
            "support": sum(1 for _, expected, _ in pairs if expected == current),
        }

    correct = sum(1 for _, expected, predicted in pairs if expected == predicted)
    macro_f1 = safe_div(sum(item["f1"] for item in per_label.values()), len(per_label))
    return {
        "total": len(pairs),
        "correct": correct,
        "accuracy": round_float(safe_div(correct, len(pairs))),
        "macro_f1": round_float(macro_f1),
        "labels": labels,
        "per_label": per_label,
        "confusion_matrix": confusion,
    }


def execution_score(case: dict[str, Any], result: dict[str, Any]) -> float:
    return sum(
        float(item["weight"]) * float(result["rubric_scores"][item["id"]])
        for item in case["expected"]["rubric"]
    )


def execution_report(
    cases: list[dict[str, Any]],
    results_by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    per_case: list[dict[str, Any]] = []
    condition_scores: dict[str, list[float]] = {condition: [] for condition in CONDITIONS}

    for case in cases:
        minimum = float(case["expected"].get("minimum_score", 0.8))
        scores = {
            condition: execution_score(case, results_by_key[(case["id"], condition)])
            for condition in CONDITIONS
        }
        for condition, score in scores.items():
            condition_scores[condition].append(score)
        per_case.append(
            {
                "id": case["id"],
                "visibility": case["visibility"],
                "minimum_score": round_float(minimum),
                "baseline_score": round_float(scores["baseline"]),
                "skill_score": round_float(scores["skill"]),
                "uplift": round_float(scores["skill"] - scores["baseline"]),
                "baseline_passed": scores["baseline"] >= minimum,
                "skill_passed": scores["skill"] >= minimum,
            }
        )

    means = {
        condition: safe_div(sum(values), len(values))
        for condition, values in condition_scores.items()
    }
    return {
        "cases": per_case,
        "baseline_mean": round_float(means["baseline"]),
        "skill_mean": round_float(means["skill"]),
        "uplift": round_float(means["skill"] - means["baseline"]),
        "regressions": [item["id"] for item in per_case if item["uplift"] < 0],
    }


def faithfulness_outcome(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    allowed = set(expected["allowed_evidence_refs"])
    used = set(result["used_evidence_refs"])
    invalid_refs = sorted(used - allowed)
    unsupported_excess = max(0, int(result["unsupported_claims"]) - int(expected["max_unsupported_claims"]))
    citation_excess = max(0, int(result["citation_errors"]) - int(expected["max_citation_errors"]))
    minimum_score = float(expected.get("minimum_score", 0.9))
    passed = (
        float(result["faithfulness_score"]) >= minimum_score
        and unsupported_excess == 0
        and citation_excess == 0
        and not invalid_refs
    )
    return {
        "score": float(result["faithfulness_score"]),
        "passed": passed,
        "unsupported_excess": unsupported_excess,
        "citation_excess": citation_excess,
        "invalid_evidence_refs": invalid_refs,
    }


def faithfulness_report(
    cases: list[dict[str, Any]],
    results_by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    per_case: list[dict[str, Any]] = []
    scores: dict[str, list[float]] = {condition: [] for condition in CONDITIONS}
    violations: dict[str, dict[str, int]] = {
        condition: {"unsupported_excess": 0, "citation_excess": 0, "invalid_evidence_refs": 0}
        for condition in CONDITIONS
    }

    for case in cases:
        outcomes = {
            condition: faithfulness_outcome(case, results_by_key[(case["id"], condition)])
            for condition in CONDITIONS
        }
        for condition, outcome in outcomes.items():
            scores[condition].append(outcome["score"])
            violations[condition]["unsupported_excess"] += outcome["unsupported_excess"]
            violations[condition]["citation_excess"] += outcome["citation_excess"]
            violations[condition]["invalid_evidence_refs"] += len(outcome["invalid_evidence_refs"])

        per_case.append(
            {
                "id": case["id"],
                "visibility": case["visibility"],
                "baseline_score": round_float(outcomes["baseline"]["score"]),
                "skill_score": round_float(outcomes["skill"]["score"]),
                "uplift": round_float(outcomes["skill"]["score"] - outcomes["baseline"]["score"]),
                "baseline_passed": outcomes["baseline"]["passed"],
                "skill_passed": outcomes["skill"]["passed"],
                "baseline_violations": {
                    key: value for key, value in outcomes["baseline"].items() if key not in {"score", "passed"}
                },
                "skill_violations": {
                    key: value for key, value in outcomes["skill"].items() if key not in {"score", "passed"}
                },
            }
        )

    means = {condition: safe_div(sum(values), len(values)) for condition, values in scores.items()}
    return {
        "cases": per_case,
        "baseline_mean": round_float(means["baseline"]),
        "skill_mean": round_float(means["skill"]),
        "uplift": round_float(means["skill"] - means["baseline"]),
        "violations": violations,
        "regressions": [item["id"] for item in per_case if item["uplift"] < 0],
    }


def build_report(
    cases_by_id: dict[str, dict[str, Any]],
    results_by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    cases = list(cases_by_id.values())
    grouped = {suite: [case for case in cases if case["suite"] == suite] for suite in SUITES}
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_counts": {
            "total": len(cases),
            "public": sum(1 for case in cases if case["visibility"] == "public"),
            "hidden": sum(1 for case in cases if case["visibility"] == "hidden"),
            "by_suite": {suite: len(grouped[suite]) for suite in SUITES},
        },
        "privacy": {
            "prompts_included": False,
            "hidden_case_ids_included": True,
            "note": "Prompts and raw responses are omitted; hidden cases are represented only by IDs and scores.",
        },
        "suites": {},
    }

    suite_baseline_scores: list[float] = []
    suite_skill_scores: list[float] = []
    regressions: list[str] = []

    if grouped["routing"]:
        baseline = routing_metrics(grouped["routing"], results_by_key, "baseline")
        skill = routing_metrics(grouped["routing"], results_by_key, "skill")
        routing_regressions = []
        routing_cases = []
        for case in grouped["routing"]:
            expected = case["expected"]["expected_skill"]
            baseline_selected = results_by_key[(case["id"], "baseline")]["selected_skill"]
            skill_selected = results_by_key[(case["id"], "skill")]["selected_skill"]
            baseline_correct = baseline_selected == expected
            skill_correct = skill_selected == expected
            if baseline_correct and not skill_correct:
                routing_regressions.append(case["id"])
            routing_cases.append(
                {
                    "id": case["id"],
                    "visibility": case["visibility"],
                    "expected_skill": expected,
                    "baseline_selected": baseline_selected,
                    "skill_selected": skill_selected,
                    "baseline_correct": baseline_correct,
                    "skill_correct": skill_correct,
                }
            )
        report["suites"]["routing"] = {
            "baseline": baseline,
            "skill": skill,
            "uplift_accuracy": round_float(skill["accuracy"] - baseline["accuracy"]),
            "cases": routing_cases,
            "regressions": routing_regressions,
        }
        suite_baseline_scores.append(float(baseline["accuracy"]))
        suite_skill_scores.append(float(skill["accuracy"]))
        regressions.extend(routing_regressions)

    if grouped["execution"]:
        execution = execution_report(grouped["execution"], results_by_key)
        report["suites"]["execution"] = execution
        suite_baseline_scores.append(float(execution["baseline_mean"]))
        suite_skill_scores.append(float(execution["skill_mean"]))
        regressions.extend(execution["regressions"])

    if grouped["faithfulness"]:
        faithfulness = faithfulness_report(grouped["faithfulness"], results_by_key)
        report["suites"]["faithfulness"] = faithfulness
        suite_baseline_scores.append(float(faithfulness["baseline_mean"]))
        suite_skill_scores.append(float(faithfulness["skill_mean"]))
        regressions.extend(faithfulness["regressions"])

    baseline_overall = safe_div(sum(suite_baseline_scores), len(suite_baseline_scores))
    skill_overall = safe_div(sum(suite_skill_scores), len(suite_skill_scores))
    report["overall"] = {
        "baseline_score": round_float(baseline_overall),
        "skill_score": round_float(skill_overall),
        "uplift": round_float(skill_overall - baseline_overall),
        "regressions": sorted(set(regressions)),
        "suite_weighting": "equal weight across available suites",
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "# Cangjie Skill Evaluation Report",
        "",
        f"- Cases: {report['case_counts']['total']} "
        f"({report['case_counts']['public']} public, {report['case_counts']['hidden']} hidden)",
        f"- Baseline score: {overall['baseline_score']:.3f}",
        f"- Skill score: {overall['skill_score']:.3f}",
        f"- Uplift: {overall['uplift']:+.3f}",
        f"- Regressions: {', '.join(overall['regressions']) if overall['regressions'] else 'none'}",
        "",
        "> Hidden prompts and raw responses are intentionally omitted.",
        "",
    ]
    routing = report["suites"].get("routing")
    if routing:
        lines.extend(
            [
                "## Routing",
                "",
                f"- Baseline accuracy: {routing['baseline']['accuracy']:.3f}",
                f"- Skill accuracy: {routing['skill']['accuracy']:.3f}",
                f"- Uplift: {routing['uplift_accuracy']:+.3f}",
                "",
            ]
        )
    execution = report["suites"].get("execution")
    if execution:
        lines.extend(
            [
                "## Execution",
                "",
                f"- Baseline mean: {execution['baseline_mean']:.3f}",
                f"- Skill mean: {execution['skill_mean']:.3f}",
                f"- Uplift: {execution['uplift']:+.3f}",
                "",
            ]
        )
    faithfulness = report["suites"].get("faithfulness")
    if faithfulness:
        lines.extend(
            [
                "## Faithfulness",
                "",
                f"- Baseline mean: {faithfulness['baseline_mean']:.3f}",
                f"- Skill mean: {faithfulness['skill_mean']:.3f}",
                f"- Uplift: {faithfulness['uplift']:+.3f}",
                "",
            ]
        )
    return "\n".join(lines)


def evaluate(
    cases_path: Path,
    results_path: Path,
) -> dict[str, Any]:
    cases = load_jsonl(cases_path)
    results = load_jsonl(results_path)
    cases_by_id = validate_cases(cases, cases_path)
    results_by_key = validate_results(results, results_path, cases_by_id)
    return build_report(cases_by_id, results_by_key)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True, help="JSONL evaluation case file")
    parser.add_argument("--results", type=Path, required=True, help="JSONL evaluator result file")
    parser.add_argument("--output", type=Path, help="write JSON report")
    parser.add_argument("--markdown-output", type=Path, help="write Markdown summary")
    parser.add_argument(
        "--minimum-uplift",
        type=float,
        default=None,
        help="exit 1 when overall uplift is below this threshold",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 when any case becomes worse with the Skill",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = evaluate(args.cases.resolve(), args.results.resolve())
    except EvaluationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json_text, encoding="utf-8")
    else:
        print(json_text, end="")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")

    failed = False
    if args.minimum_uplift is not None and report["overall"]["uplift"] < args.minimum_uplift:
        print(
            f"EVALUATION GATE FAILED: uplift {report['overall']['uplift']:.6f} "
            f"< minimum {args.minimum_uplift:.6f}",
            file=sys.stderr,
        )
        failed = True
    if args.fail_on_regression and report["overall"]["regressions"]:
        print(
            "EVALUATION GATE FAILED: regressions: " + ", ".join(report["overall"]["regressions"]),
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

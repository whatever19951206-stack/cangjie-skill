#!/usr/bin/env python3
"""Validate machine-readable Cangjie skill bundles.

A bundle is a directory containing:
  skill.yaml
  evidence.json
  test-prompts.json
  test-results.json

The validator performs JSON Schema validation plus cross-file integrity checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
PLACEHOLDER_MARKERS = ("{{", "}}", "TODO", "TBD", "待定", "占位")


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_schema(name: str) -> dict[str, Any]:
    return load_json(SCHEMA_DIR / name)


def schema_issues(instance: Any, schema_name: str, path: Path) -> list[ValidationIssue]:
    validator = Draft202012Validator(
        load_schema(schema_name),
        format_checker=FormatChecker(),
    )
    issues: list[ValidationIssue] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        issues.append(ValidationIssue(path, f"schema violation at {location}: {error.message}"))
    return issues


def walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)


def placeholder_issues(value: Any, path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for text in walk_strings(value):
        for marker in PLACEHOLDER_MARKERS:
            if marker in text:
                issues.append(ValidationIssue(path, f"unresolved placeholder marker {marker!r} in {text!r}"))
                break
    return issues


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_evidence_integrity(evidence_doc: dict[str, Any], path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    source_id = evidence_doc.get("source", {}).get("source_id")
    seen: set[str] = set()

    for index, item in enumerate(evidence_doc.get("evidence", [])):
        evidence_id = item.get("evidence_id")
        if evidence_id in seen:
            issues.append(ValidationIssue(path, f"duplicate evidence_id: {evidence_id}"))
        seen.add(evidence_id)

        text = item.get("text")
        expected_hash = item.get("text_hash")
        if isinstance(text, str) and isinstance(expected_hash, str):
            actual_hash = sha256_text(text)
            if actual_hash != expected_hash:
                issues.append(
                    ValidationIssue(
                        path,
                        f"evidence[{index}] {evidence_id}: text_hash mismatch; expected {actual_hash}",
                    )
                )

        claim_type = item.get("claim_type")
        item_source_id = item.get("source_ref", {}).get("source_id")
        if claim_type != "external_fact" and source_id and item_source_id != source_id:
            issues.append(
                ValidationIssue(
                    path,
                    f"evidence[{index}] {evidence_id}: source_ref.source_id must equal bundle source_id {source_id!r}",
                )
            )

    return issues


def validate_test_contract(
    prompts: dict[str, Any],
    results: dict[str, Any],
    prompt_path: Path,
    result_path: Path,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    prompt_cases = prompts.get("test_cases", [])
    result_cases = results.get("tests", [])

    prompt_by_id = {case.get("id"): case for case in prompt_cases}
    result_by_id = {case.get("id"): case for case in result_cases}

    if len(prompt_by_id) != len(prompt_cases):
        issues.append(ValidationIssue(prompt_path, "test case IDs must be unique"))
    if len(result_by_id) != len(result_cases):
        issues.append(ValidationIssue(result_path, "result test IDs must be unique"))

    prompt_ids = set(prompt_by_id)
    result_ids = set(result_by_id)
    missing = sorted(prompt_ids - result_ids)
    extra = sorted(result_ids - prompt_ids)
    if missing:
        issues.append(ValidationIssue(result_path, f"missing executed results for test IDs: {missing}"))
    if extra:
        issues.append(ValidationIssue(result_path, f"results contain unknown test IDs: {extra}"))

    counts = {"should_trigger": 0, "should_not_trigger": 0, "edge_case": 0}
    sibling_negative_count = 0
    for case in prompt_cases:
        case_type = case.get("type")
        if case_type in counts:
            counts[case_type] += 1
        if case_type == "should_not_trigger" and case.get("sibling_skill"):
            sibling_negative_count += 1

    if counts["should_trigger"] < 3:
        issues.append(ValidationIssue(prompt_path, "requires at least 3 should_trigger tests"))
    if counts["should_not_trigger"] < 2:
        issues.append(ValidationIssue(prompt_path, "requires at least 2 should_not_trigger tests"))
    if counts["edge_case"] < 1:
        issues.append(ValidationIssue(prompt_path, "requires at least 1 edge_case test"))
    if sibling_negative_count < 1:
        issues.append(ValidationIssue(prompt_path, "requires at least 1 sibling-skill negative test"))

    for case_id in sorted(prompt_ids & result_ids):
        prompt_case = prompt_by_id[case_id]
        result_case = result_by_id[case_id]
        if prompt_case.get("type") != result_case.get("type"):
            issues.append(
                ValidationIssue(
                    result_path,
                    f"test {case_id}: result type {result_case.get('type')!r} does not match prompt type {prompt_case.get('type')!r}",
                )
            )

    total = len(result_cases)
    passed = sum(1 for case in result_cases if case.get("passed") is True)
    failed = total - passed
    pass_rate = passed / total if total else 0.0
    negative_results = [case for case in result_cases if case.get("type") == "should_not_trigger"]
    all_negative_passed = bool(negative_results) and all(case.get("passed") is True for case in negative_results)
    summary = results.get("summary", {})

    expected_summary = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "all_negative_passed": all_negative_passed,
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            issues.append(ValidationIssue(result_path, f"summary.{key} must be {expected!r}"))

    reported_rate = summary.get("pass_rate")
    if not isinstance(reported_rate, (int, float)) or not math.isclose(reported_rate, pass_rate, abs_tol=1e-9):
        issues.append(ValidationIssue(result_path, f"summary.pass_rate must be {pass_rate:.12g}"))

    minimum_pass_rate = prompts.get("minimum_pass_rate", 1.0)
    expected_status = "passed" if pass_rate >= minimum_pass_rate and all_negative_passed else "failed"
    if summary.get("status") != expected_status:
        issues.append(ValidationIssue(result_path, f"summary.status must be {expected_status!r}"))
    if expected_status != "passed":
        issues.append(
            ValidationIssue(
                result_path,
                f"quality threshold failed: pass_rate={pass_rate:.3f}, minimum={minimum_pass_rate:.3f}, "
                f"all_negative_passed={all_negative_passed}",
            )
        )

    return issues


def validate_bundle(bundle_dir: Path) -> list[ValidationIssue]:
    bundle_dir = bundle_dir.resolve()
    required_files = {
        "skill": bundle_dir / "skill.yaml",
        "evidence": bundle_dir / "evidence.json",
        "prompts": bundle_dir / "test-prompts.json",
        "results": bundle_dir / "test-results.json",
    }
    issues: list[ValidationIssue] = []

    for path in required_files.values():
        if not path.is_file():
            issues.append(ValidationIssue(path, "required file is missing"))
    if issues:
        return issues

    try:
        skill = load_yaml(required_files["skill"])
        evidence = load_json(required_files["evidence"])
        prompts = load_json(required_files["prompts"])
        results = load_json(required_files["results"])
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return [ValidationIssue(bundle_dir, f"could not parse bundle: {exc}")]

    documents = (
        (skill, "skill.schema.json", required_files["skill"]),
        (evidence, "evidence.schema.json", required_files["evidence"]),
        (prompts, "test-prompts.schema.json", required_files["prompts"]),
        (results, "test-results.schema.json", required_files["results"]),
    )
    for document, schema_name, path in documents:
        issues.extend(schema_issues(document, schema_name, path))
        issues.extend(placeholder_issues(document, path))

    if issues:
        return issues

    skill_name = skill["name"]
    if prompts["skill"] != skill_name:
        issues.append(ValidationIssue(required_files["prompts"], "skill name does not match skill.yaml"))
    if results["skill"] != skill_name:
        issues.append(ValidationIssue(required_files["results"], "skill name does not match skill.yaml"))

    skill_source = skill["source"]
    evidence_source = evidence["source"]
    if skill_source["source_id"] != evidence_source["source_id"]:
        issues.append(ValidationIssue(required_files["evidence"], "source_id does not match skill.yaml"))
    if skill_source.get("source_hash") and evidence_source.get("source_hash"):
        if skill_source["source_hash"] != evidence_source["source_hash"]:
            issues.append(ValidationIssue(required_files["evidence"], "source_hash does not match skill.yaml"))

    evidence_ids = {item["evidence_id"] for item in evidence["evidence"]}
    missing_refs = sorted(set(skill["evidence_refs"]) - evidence_ids)
    if missing_refs:
        issues.append(ValidationIssue(required_files["skill"], f"unknown evidence_refs: {missing_refs}"))

    if skill["verification"]["hard_gate_passed"] is not True:
        issues.append(ValidationIssue(required_files["skill"], "verification.hard_gate_passed must be true"))
    if skill["status"] in {"tested", "published"} and results["summary"]["status"] != "passed":
        issues.append(ValidationIssue(required_files["skill"], "tested/published skill must have passing results"))

    issues.extend(validate_evidence_integrity(evidence, required_files["evidence"]))
    issues.extend(
        validate_test_contract(
            prompts,
            results,
            required_files["prompts"],
            required_files["results"],
        )
    )
    return issues


def validate_pipeline_state(path: Path) -> list[ValidationIssue]:
    try:
        document = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [ValidationIssue(path, f"could not parse pipeline state: {exc}")]
    issues = schema_issues(document, "pipeline-state.schema.json", path)
    issues.extend(placeholder_issues(document, path))
    return issues


def discover_bundles(root: Path) -> list[Path]:
    ignored = {".git", ".venv", "venv", "node_modules"}
    bundles: list[Path] = []
    for path in root.rglob("skill.yaml"):
        if not any(part in ignored for part in path.parts):
            bundles.append(path.parent)
    return sorted(set(bundles))


def run(paths: list[Path], scan_all: bool) -> int:
    bundle_dirs = discover_bundles(ROOT) if scan_all else paths
    if not bundle_dirs:
        print("ERROR: no skill bundles found", file=sys.stderr)
        return 2

    all_issues: list[ValidationIssue] = []
    for bundle_dir in bundle_dirs:
        issues = validate_bundle(bundle_dir)
        if issues:
            all_issues.extend(issues)
            print(f"FAIL: {bundle_dir}")
        else:
            print(f"PASS: {bundle_dir}")

    if scan_all:
        for state_path in sorted(ROOT.rglob("pipeline-state.json")):
            issues = validate_pipeline_state(state_path)
            if issues:
                all_issues.extend(issues)
                print(f"FAIL: {state_path}")
            else:
                print(f"PASS: {state_path}")

    if all_issues:
        print("\nQuality gate errors:", file=sys.stderr)
        for issue in all_issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="bundle directories to validate")
    parser.add_argument("--all", action="store_true", help="scan the repository for every skill.yaml bundle")
    args = parser.parse_args()
    if not args.all and not args.paths:
        parser.error("provide at least one bundle directory or use --all")
    return args


def main() -> int:
    args = parse_args()
    return run(args.paths, args.all)


if __name__ == "__main__":
    raise SystemExit(main())

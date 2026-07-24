#!/usr/bin/env python3
"""Scaffold machine-readable files from a legacy Cangjie SKILL.md bundle.

This migration is deliberately conservative:
- it never fabricates executed test results;
- it marks verification.hard_gate_passed=false and status=draft;
- it requires at least one source quote before creating evidence.json;
- it refuses to overwrite machine-readable files unless --force is supplied.

After migration, review the generated files, execute the tests, write
``test-results.json``, set verification scores, and run ``quality_gate.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class MigrationError(RuntimeError):
    """Raised when a legacy bundle cannot be migrated safely."""


@dataclass
class MigrationResult:
    skill: dict[str, Any]
    evidence: dict[str, Any]
    test_prompts: dict[str, Any] | None
    report: dict[str, Any]


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "legacy-source"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n", text, flags=re.DOTALL)
    if not match:
        raise MigrationError("SKILL.md is missing YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise MigrationError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise MigrationError("SKILL.md frontmatter must be a mapping")
    return frontmatter, text[match.end() :]


def extract_h2(body: str, heading_prefix: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading_prefix)}[^\n]*\n(.*?)(?=^##\s+|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    return match.group(1).strip() if match else ""


def extract_h3_subsection(section: str, heading_text: str) -> str:
    pattern = re.compile(
        rf"^###\s+{re.escape(heading_text)}[^\n]*\n(.*?)(?=^###\s+|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(section)
    return match.group(1).strip() if match else ""


def extract_title(body: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", body, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback


def extract_quote(reading_section: str) -> str:
    quote_lines: list[str] = []
    for raw_line in reading_section.splitlines():
        match = re.match(r"^\s*>\s?(.*)$", raw_line)
        if not match:
            if quote_lines:
                break
            continue
        line = match.group(1).strip()
        if not line:
            if quote_lines:
                quote_lines.append("")
            continue
        if line.startswith(("—", "- ")):
            break
        quote_lines.append(line)
    quote = "\n".join(quote_lines).strip()
    if not quote:
        raise MigrationError("R — 原文 section has no usable blockquote; evidence cannot be fabricated")
    return quote


def extract_numbered_items(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\s*\d+[.)]\s+(.+?)\s*$", text, flags=re.MULTILINE)
        if match.group(1).strip()
    ]


def extract_bullets(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\s*[-*]\s+(.+?)\s*$", text, flags=re.MULTILINE)
        if match.group(1).strip()
    ]


def strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in {'"', "'", "“", "‘"} and value[-1] in {'"', "'", "”", "’"}:
        return value[1:-1].strip()
    return value


def extract_workflow(execution_section: str) -> list[dict[str, str]]:
    lines = execution_section.splitlines()
    steps: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in lines:
        step_match = re.match(r"^\s*\d+[.)]\s+\*\*(.+?)\*\*", raw_line)
        if step_match:
            if current:
                steps.append(current)
            action = step_match.group(1).strip().rstrip("：:")
            current = {
                "id": f"step_{len(steps) + 1}",
                "action": action,
                "completion": "完成本步骤在 legacy SKILL.md 中规定的检查",
            }
            continue

        if current:
            completion_match = re.match(r"^\s*[-*]\s+完成标准\s*[:：]\s*(.+?)\s*$", raw_line)
            if completion_match:
                current["completion"] = completion_match.group(1).strip()
                continue
            stop_match = re.match(r"^\s*[-*]\s+判停条件\s*[:：]\s*(.+?)\s*$", raw_line)
            if stop_match:
                current["stop_if"] = stop_match.group(1).strip()

    if current:
        steps.append(current)
    if not steps:
        raise MigrationError("E — 可执行步骤 section has no numbered bold steps")
    return steps


def parse_source(source_book: str, source_chapter: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    value = source_book.strip()
    title = value
    author = ""

    chinese_book = re.match(r"^《(.+?)》\s*(.*)$", value)
    if chinese_book:
        title = chinese_book.group(1).strip()
        author = chinese_book.group(2).strip()
    elif " — " in value:
        title, author = (part.strip() for part in value.split(" — ", 1))
    elif " - " in value:
        title, author = (part.strip() for part in value.split(" - ", 1))

    source_seed = value or title
    source_id = f"{slugify(title)}-{hashlib.sha256(source_seed.encode('utf-8')).hexdigest()[:8]}"
    source = {
        "source_id": source_id,
        "title": title or "Legacy source",
        "type": "book",
    }
    if author:
        source["author"] = author

    source_ref: dict[str, Any] = {"source_id": source_id}
    if source_chapter:
        source_ref["chapter"] = str(source_chapter).strip()
    else:
        source_ref["chapter"] = "legacy SKILL.md source location"
    return source, source_ref


def normalize_test_prompts(path: Path, skill_name: str, warnings: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        warnings.append("legacy test-prompts.json not found; create routing tests before validation")
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"legacy test-prompts.json could not be parsed: {exc}")
        return None
    if not isinstance(document, dict):
        warnings.append("legacy test-prompts.json is not an object")
        return None

    normalized_cases: list[dict[str, Any]] = []
    for index, case in enumerate(document.get("test_cases", []), start=1):
        if not isinstance(case, dict):
            warnings.append(f"test case #{index} is not an object and was skipped")
            continue
        case_type = case.get("type", case.get("category"))
        prompt = str(case.get("prompt") or "").strip()
        expected_behavior = str(case.get("expected_behavior") or "").strip()
        if case_type not in {"should_trigger", "should_not_trigger", "edge_case"}:
            warnings.append(f"test case #{index} has invalid type {case_type!r} and was skipped")
            continue
        if len(prompt) < 4 or len(expected_behavior) < 4:
            warnings.append(f"test case #{index} has an empty/short prompt or expected behavior and was skipped")
            continue
        normalized: dict[str, Any] = {
            "id": str(case.get("id") or f"legacy-{index:02d}"),
            "type": case_type,
            "prompt": prompt,
            "expected_behavior": expected_behavior,
        }
        if case.get("notes"):
            normalized["notes"] = str(case["notes"])
        if case.get("sibling_skill"):
            normalized["sibling_skill"] = str(case["sibling_skill"])
        normalized_cases.append(normalized)

    if not normalized_cases:
        warnings.append("legacy test-prompts.json contained no usable test cases")
        return None

    type_counts = {
        kind: sum(1 for case in normalized_cases if case.get("type") == kind)
        for kind in ("should_trigger", "should_not_trigger", "edge_case")
    }
    if type_counts["should_trigger"] < 3 or type_counts["should_not_trigger"] < 2 or type_counts["edge_case"] < 1:
        warnings.append("migrated tests do not yet meet the 3 positive / 2 negative / 1 edge minimum")
    if not any(case.get("type") == "should_not_trigger" and case.get("sibling_skill") for case in normalized_cases):
        warnings.append("no explicit sibling_skill negative test was found; add one before validation")

    return {
        "skill": skill_name,
        "version": str(document.get("version") or "0.1.0"),
        "source_book": str(document.get("source_book") or "Legacy source"),
        "darwin_compatible": bool(document.get("darwin_compatible", True)),
        "test_cases": normalized_cases,
        "minimum_pass_rate": float(document.get("minimum_pass_rate", 0.8)),
        "notes": "Migrated from legacy test-prompts.json; review all cases before execution.",
    }


def build_migration(skill_dir: Path) -> MigrationResult:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise MigrationError(f"missing {skill_md}")

    raw_text = skill_md.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw_text)
    skill_name = str(frontmatter.get("name") or skill_dir.name).strip()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name):
        raise MigrationError(f"invalid skill name {skill_name!r}; expected lowercase kebab-case")

    description = str(frontmatter.get("description") or "").strip()
    if len(description) < 20:
        raise MigrationError("frontmatter description is missing or too short")

    source_book = str(frontmatter.get("source_book") or "Legacy source").strip()
    source_chapter = frontmatter.get("source_chapter")
    source, source_ref = parse_source(source_book, str(source_chapter) if source_chapter else None)

    reading = extract_h2(body, "R —")
    a2 = extract_h2(body, "A2 —")
    execution = extract_h2(body, "E —")
    boundary = extract_h2(body, "B —")
    quote = extract_quote(reading)

    trigger_text = extract_h3_subsection(a2, "用户会在什么情境下需要这个 skill?")
    signal_text = extract_h3_subsection(a2, "语言信号")
    exclusion_text = extract_h3_subsection(boundary, "不要在以下情况使用此 skill")

    include = extract_numbered_items(trigger_text) or [description]
    signals = [strip_wrapping_quotes(item) for item in extract_bullets(signal_text)]
    exclude = extract_bullets(exclusion_text) or ["纯事实查询或与本方法无关的请求"]
    workflow = extract_workflow(execution)

    evidence_id = "ev_legacy_001"
    evidence = {
        "schema_version": "1.0",
        "source": source,
        "evidence": [
            {
                "evidence_id": evidence_id,
                "claim_type": "direct_quote",
                "source_ref": source_ref,
                "text": quote,
                "text_hash": sha256_text(quote),
                "supports": "支持 legacy Skill 中方法论的核心来源主张",
                "notes": "由迁移脚本从 R — 原文段提取；必须人工核对位置和版本。",
            }
        ],
    }

    routing: dict[str, Any] = {"include": include, "exclude": exclude}
    if signals:
        routing["signals"] = signals

    skill = {
        "schema_version": "1.0",
        "name": skill_name,
        "title": extract_title(body, skill_name),
        "description": description,
        "source": source,
        "routing": routing,
        "workflow": workflow,
        "boundaries": exclude,
        "evidence_refs": [evidence_id],
        "verification": {
            "faithfulness": 0.5,
            "evidence_diversity": 0.0,
            "transferability": 0.0,
            "uplift": None,
            "separability": 0.0,
            "risk_level": "medium",
            "hard_gate_passed": False,
        },
        "status": "draft",
    }

    warnings = [
        "verification scores are conservative placeholders and hard_gate_passed is false",
        "only the first R-section quote was migrated; add independent evidence before publishing",
        "source type defaults to book; correct it for video, podcast, course, interview, article, or document-set",
        "test-results.json was intentionally not generated because tests must be executed, not fabricated",
    ]
    test_prompts = normalize_test_prompts(skill_dir / "test-prompts.json", skill_name, warnings)

    created_files = ["skill.yaml", "evidence.json", "migration-report.json"]
    if test_prompts is not None:
        created_files.append("test-prompts.migrated.json")
    report = {
        "migration_version": "1.0",
        "status": "needs_review",
        "skill": skill_name,
        "created_files": created_files,
        "warnings": warnings,
        "next_steps": [
            "人工核对 source、routing、workflow、boundaries 和证据定位",
            "补充至少两条独立证据并更新 verification 分数",
            "完善 test-prompts.json，执行盲测并生成 test-results.json",
            "将 hard_gate_passed 设为 true、status 设为 tested 后运行 quality_gate.py",
        ],
    }
    return MigrationResult(skill=skill, evidence=evidence, test_prompts=test_prompts, report=report)


def write_result(skill_dir: Path, result: MigrationResult, force: bool) -> None:
    outputs: dict[str, str] = {
        "skill.yaml": yaml.safe_dump(result.skill, allow_unicode=True, sort_keys=False),
        "evidence.json": json.dumps(result.evidence, ensure_ascii=False, indent=2) + "\n",
        "migration-report.json": json.dumps(result.report, ensure_ascii=False, indent=2) + "\n",
    }
    if result.test_prompts is not None:
        test_prompt_name = "test-prompts.json" if force else "test-prompts.migrated.json"
        outputs[test_prompt_name] = json.dumps(result.test_prompts, ensure_ascii=False, indent=2) + "\n"

    conflicts = [name for name in outputs if (skill_dir / name).exists()]
    if conflicts and not force:
        raise MigrationError(f"refusing to overwrite existing files: {', '.join(conflicts)}; use --force")
    for name, content in outputs.items():
        (skill_dir / name).write_text(content, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", type=Path, help="legacy skill directory containing SKILL.md")
    parser.add_argument("--write", action="store_true", help="write scaffold files into the skill directory")
    parser.add_argument("--force", action="store_true", help="overwrite existing machine-readable files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    skill_dir = args.skill_dir.resolve()
    try:
        result = build_migration(skill_dir)
        if args.write:
            write_result(skill_dir, result, args.force)
            print(f"Migrated scaffold written to {skill_dir}")
        else:
            preview = {
                "skill": result.skill,
                "evidence": result.evidence,
                "test_prompts": result.test_prompts,
                "report": result.report,
            }
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        print("Review required: no test-results.json was generated and hard_gate_passed remains false.")
        return 0
    except (MigrationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

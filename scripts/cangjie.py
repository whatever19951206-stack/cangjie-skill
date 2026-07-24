#!/usr/bin/env python3
"""Cangjie project CLI for modes, Map-Reduce planning, resume, and export.

The CLI intentionally does not embed a model provider. It creates deterministic
plans and machine state that any agent runtime can execute, while reusing the P0
quality gate and P1 evaluation harness.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
PIPELINE_VERSION = "2.0.0"
EXTRACTORS = ("framework", "principle", "case", "counter-example", "glossary")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

MODE_PROFILES: dict[str, dict[str, Any]] = {
    "scan": {
        "extractors": ["framework", "principle"],
        "max_candidates": 12,
        "max_skills": 3,
        "independent_evaluator": False,
        "hidden_evaluation": False,
        "external_validation": False,
        "description": "快速判断材料是否值得蒸馏；只做少量候选，不作为正式发布。",
    },
    "standard": {
        "extractors": list(EXTRACTORS),
        "max_candidates": 30,
        "max_skills": 5,
        "independent_evaluator": True,
        "hidden_evaluation": False,
        "external_validation": False,
        "description": "个人长期使用的默认模式；完整证据链、测试和公开 A/B 评测。",
    },
    "audit": {
        "extractors": list(EXTRACTORS),
        "max_candidates": 60,
        "max_skills": 10,
        "independent_evaluator": True,
        "hidden_evaluation": True,
        "external_validation": True,
        "description": "高风险或组织级使用；要求隐藏评测、外部核验和完整审计。",
    },
}


class CangjieError(RuntimeError):
    """Raised for invalid project operations."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise CangjieError(f"could not hash {path}: {exc}") from exc


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_write_yaml(path: Path, value: Any) -> None:
    atomic_write_text(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CangjieError(f"could not read JSON {path}: {exc}") from exc


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CangjieError(f"could not read YAML {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CangjieError(f"could not read JSONL {path}: {exc}") from exc
    values: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CangjieError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise CangjieError(f"{path}:{line_number}: each record must be an object")
        values.append(value)
    if not values:
        raise CangjieError(f"{path}: no records found")
    return values


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    atomic_write_text(
        path,
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
    )


def validate_schema(value: Any, schema_name: str, label: str) -> None:
    schema = load_json(SCHEMA_DIR / schema_name)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        messages = []
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            messages.append(f"{label}: {location}: {error.message}")
        raise CangjieError("schema validation failed:\n- " + "\n- ".join(messages))


def project_paths(project_dir: Path) -> dict[str, Path]:
    return {
        "project": project_dir / "project.yaml",
        "state": project_dir / "pipeline-state.json",
        "chunks": project_dir / "source" / "chunks.jsonl",
        "plan": project_dir / "work" / "extraction-plan.json",
    }


def load_project(project_dir: Path) -> dict[str, Any]:
    path = project_paths(project_dir)["project"]
    if not path.is_file():
        raise CangjieError(f"missing project configuration: {path}")
    project = load_yaml(path)
    validate_schema(project, "project.schema.json", str(path))
    return project


def load_state(project_dir: Path) -> dict[str, Any]:
    path = project_paths(project_dir)["state"]
    if not path.is_file():
        raise CangjieError(f"missing pipeline state: {path}")
    state = load_json(path)
    validate_schema(state, "pipeline-state.schema.json", str(path))
    return state


def save_state(project_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    path = project_paths(project_dir)["state"]
    validate_schema(state, "pipeline-state.schema.json", str(path))
    atomic_write_json(path, state)


def init_project(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    paths = project_paths(project_dir)
    if paths["project"].exists() and not args.force:
        raise CangjieError(f"project already exists: {paths['project']}; use --force to replace configuration")

    if args.source_file:
        source_file = args.source_file.resolve()
        if not source_file.is_file():
            raise CangjieError(f"source file not found: {source_file}")
        source_hash = sha256_file(source_file)
    else:
        source_hash = args.source_hash
    if not isinstance(source_hash, str) or not SHA256_PATTERN.fullmatch(source_hash):
        raise CangjieError("source hash must use sha256:<64 lowercase hex>")

    profile = MODE_PROFILES[args.mode]
    timestamp = now_iso()
    project: dict[str, Any] = {
        "schema_version": "1.0",
        "project_id": args.project_id,
        "mode": args.mode,
        "pipeline_version": PIPELINE_VERSION,
        "source": {
            "source_id": args.source_id,
            "title": args.source_title,
            "type": args.source_type,
            "source_hash": source_hash,
        },
        "goal": args.goal,
        "limits": {
            "max_candidates": profile["max_candidates"],
            "max_skills": profile["max_skills"],
        },
        "requirements": {
            "independent_evaluator": profile["independent_evaluator"],
            "hidden_evaluation": profile["hidden_evaluation"],
            "external_validation": profile["external_validation"],
        },
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    if args.author:
        project["source"]["author"] = args.author
    if args.year is not None:
        project["source"]["year"] = args.year

    validate_schema(project, "project.schema.json", str(paths["project"]))
    for relative in ("source", "work", "skills", "evals", "reports", "dist"):
        (project_dir / relative).mkdir(parents=True, exist_ok=True)
    atomic_write_yaml(paths["project"], project)

    state = {
        "run_id": f"run-{uuid.uuid4().hex[:12]}",
        "project_id": args.project_id,
        "mode": args.mode,
        "pipeline_version": PIPELINE_VERSION,
        "source_hash": source_hash,
        "stage": "initialized",
        "status": "pending",
        "completed_tasks": [],
        "pending_tasks": ["chunk_source", "build_extraction_plan"],
        "artifacts": {"project": "project.yaml"},
        "task_cache": {},
        "model_runs": [],
        "updated_at": timestamp,
    }
    save_state(project_dir, state)
    return {"project_dir": str(project_dir), "project": project, "state": state}


def split_text(text: str, max_chars: int) -> list[tuple[int, int, str]]:
    if not text:
        raise CangjieError("source text is empty")
    if max_chars < 200:
        raise CangjieError("--max-chars must be at least 200")
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_chars)
        end = hard_end
        if hard_end < len(text):
            search_start = start + max_chars // 2
            paragraph_break = text.rfind("\n\n", search_start, hard_end)
            line_break = text.rfind("\n", search_start, hard_end)
            candidate = max(
                paragraph_break + 2 if paragraph_break >= 0 else -1,
                line_break + 1 if line_break >= 0 else -1,
            )
            if candidate > start:
                end = candidate
        value = text[start:end]
        if not value:
            end = min(len(text), start + max_chars)
            value = text[start:end]
        chunks.append((start, end, value))
        start = end
    return chunks


def chunk_source(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    project = load_project(project_dir)
    source_file = args.source_file.resolve()
    if not source_file.is_file():
        raise CangjieError(f"source file not found: {source_file}")
    actual_hash = sha256_file(source_file)
    expected_hash = project["source"]["source_hash"]
    if actual_hash != expected_hash:
        raise CangjieError(f"source hash mismatch: project={expected_hash}, file={actual_hash}")
    try:
        text = source_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CangjieError(f"could not read source text: {exc}") from exc

    values = [
        {
            "chunk_id": f"chunk-{index:04d}",
            "content_hash": sha256_text(content),
            "source_ref": f"chars:{start + 1}-{end}",
        }
        for index, (start, end, content) in enumerate(split_text(text, args.max_chars), start=1)
    ]
    chunks_path = project_paths(project_dir)["chunks"]
    write_jsonl(chunks_path, values)

    state = load_state(project_dir)
    state["stage"] = "chunked"
    state["status"] = "pending"
    state["completed_tasks"] = sorted(set(state["completed_tasks"] + ["chunk_source"]))
    state["pending_tasks"] = [task for task in state["pending_tasks"] if task != "chunk_source"]
    if "build_extraction_plan" not in state["pending_tasks"]:
        state["pending_tasks"].append("build_extraction_plan")
    state["artifacts"]["chunks"] = str(chunks_path.relative_to(project_dir))
    save_state(project_dir, state)
    return {"chunks": len(values), "path": str(chunks_path), "source_hash": actual_hash}


def cached_task_status(project_dir: Path, state: dict[str, Any], task_id: str, cache_key: str, artifact: str) -> str:
    cached = state.get("task_cache", {}).get(task_id)
    if not cached or cached.get("status") != "success" or cached.get("cache_key") != cache_key:
        return "pending"
    cached_artifact = cached.get("artifact") or artifact
    return "success" if cached_artifact and (project_dir / cached_artifact).is_file() else "pending"


def build_extraction_plan(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    project = load_project(project_dir)
    state = load_state(project_dir)
    chunks_path = args.chunks.resolve() if args.chunks else project_paths(project_dir)["chunks"]
    chunks = read_jsonl(chunks_path)
    seen_chunks: set[str] = set()
    for chunk in chunks:
        missing = sorted({"chunk_id", "content_hash", "source_ref"} - set(chunk))
        if missing:
            raise CangjieError(f"chunk record missing fields {missing}: {chunk}")
        if chunk["chunk_id"] in seen_chunks:
            raise CangjieError(f"duplicate chunk_id: {chunk['chunk_id']}")
        seen_chunks.add(chunk["chunk_id"])

    profile = MODE_PROFILES[project["mode"]]
    tasks: list[dict[str, Any]] = []
    map_ids_by_extractor: dict[str, list[str]] = {}
    for extractor in profile["extractors"]:
        map_ids: list[str] = []
        for chunk in chunks:
            task_id = f"map.{extractor}.{chunk['chunk_id']}"
            artifact = f"work/map/{extractor}/{chunk['chunk_id']}.json"
            cache_key = canonical_hash(
                {
                    "stage": "map",
                    "source_hash": project["source"]["source_hash"],
                    "chunk_hash": chunk["content_hash"],
                    "extractor": extractor,
                    "prompt_version": args.prompt_version,
                    "model_id": args.model_id,
                }
            )
            tasks.append(
                {
                    "task_id": task_id,
                    "stage": "map",
                    "extractor": extractor,
                    "chunk_id": chunk["chunk_id"],
                    "dependencies": [],
                    "cache_key": cache_key,
                    "status": cached_task_status(project_dir, state, task_id, cache_key, artifact),
                    "artifact": artifact,
                }
            )
            map_ids.append(task_id)
        map_ids_by_extractor[extractor] = map_ids

    reduce_ids: list[str] = []
    for extractor in profile["extractors"]:
        map_ids = map_ids_by_extractor[extractor]
        dependency_keys = [task["cache_key"] for task in tasks if task["task_id"] in map_ids]
        task_id = f"reduce.{extractor}"
        artifact = f"work/reduced/{extractor}.json"
        cache_key = canonical_hash(
            {
                "stage": "reduce",
                "extractor": extractor,
                "dependencies": dependency_keys,
                "prompt_version": args.prompt_version,
                "model_id": args.model_id,
            }
        )
        tasks.append(
            {
                "task_id": task_id,
                "stage": "reduce",
                "extractor": extractor,
                "chunk_id": None,
                "dependencies": map_ids,
                "cache_key": cache_key,
                "status": cached_task_status(project_dir, state, task_id, cache_key, artifact),
                "artifact": artifact,
            }
        )
        reduce_ids.append(task_id)

    merge_key = canonical_hash(
        {
            "stage": "merge",
            "dependencies": [task["cache_key"] for task in tasks if task["task_id"] in reduce_ids],
            "max_candidates": project["limits"]["max_candidates"],
        }
    )
    merge_artifact = "work/candidates.json"
    tasks.append(
        {
            "task_id": "merge.candidates",
            "stage": "merge",
            "extractor": "cross-extractor",
            "chunk_id": None,
            "dependencies": reduce_ids,
            "cache_key": merge_key,
            "status": cached_task_status(project_dir, state, "merge.candidates", merge_key, merge_artifact),
            "artifact": merge_artifact,
        }
    )
    verify_key = canonical_hash(
        {
            "stage": "verify",
            "merge_key": merge_key,
            "mode": project["mode"],
            "max_skills": project["limits"]["max_skills"],
            "external_validation": project["requirements"]["external_validation"],
        }
    )
    verify_artifact = "work/verified.json"
    tasks.append(
        {
            "task_id": "verify.candidates",
            "stage": "verify",
            "extractor": "verification",
            "chunk_id": None,
            "dependencies": ["merge.candidates"],
            "cache_key": verify_key,
            "status": cached_task_status(project_dir, state, "verify.candidates", verify_key, verify_artifact),
            "artifact": verify_artifact,
        }
    )

    plan = {
        "schema_version": "1.0",
        "project_id": project["project_id"],
        "mode": project["mode"],
        "source_hash": project["source"]["source_hash"],
        "prompt_version": args.prompt_version,
        "model_id": args.model_id,
        "chunks": chunks,
        "tasks": tasks,
        "generated_at": now_iso(),
    }
    plan_path = project_paths(project_dir)["plan"]
    validate_schema(plan, "extraction-plan.schema.json", str(plan_path))
    atomic_write_json(plan_path, plan)

    prior_completed = set(state["completed_tasks"])
    cached_completed = {task["task_id"] for task in tasks if task["status"] == "success"}
    state["stage"] = "extraction-planned"
    state["status"] = "pending"
    state["completed_tasks"] = sorted(prior_completed | cached_completed | {"build_extraction_plan"})
    state["pending_tasks"] = [task["task_id"] for task in tasks if task["status"] != "success"]
    state["artifacts"]["extraction_plan"] = str(plan_path.relative_to(project_dir))
    save_state(project_dir, state)
    return {
        "path": str(plan_path),
        "mode": project["mode"],
        "chunks": len(chunks),
        "tasks": len(tasks),
        "cached": sum(1 for task in tasks if task["status"] == "success"),
        "pending": sum(1 for task in tasks if task["status"] != "success"),
    }


def record_task(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    plan_path = project_paths(project_dir)["plan"]
    if not plan_path.is_file():
        raise CangjieError(f"missing extraction plan: {plan_path}")
    plan = load_json(plan_path)
    validate_schema(plan, "extraction-plan.schema.json", str(plan_path))
    task = next((item for item in plan["tasks"] if item["task_id"] == args.task_id), None)
    if task is None:
        raise CangjieError(f"unknown task_id: {args.task_id}")

    artifact = args.artifact or task.get("artifact")
    if args.status == "success":
        if not artifact:
            raise CangjieError("successful task requires an artifact path")
        if not (project_dir / artifact).is_file():
            raise CangjieError(f"successful task artifact does not exist: {project_dir / artifact}")
    task["status"] = args.status
    if artifact:
        task["artifact"] = artifact
    atomic_write_json(plan_path, plan)

    state = load_state(project_dir)
    state.setdefault("task_cache", {})[args.task_id] = {
        "cache_key": task["cache_key"],
        "status": args.status,
        "artifact": artifact,
        "updated_at": now_iso(),
    }
    state["pending_tasks"] = [value for value in state["pending_tasks"] if value != args.task_id]
    if args.status == "success":
        state["completed_tasks"] = sorted(set(state["completed_tasks"] + [args.task_id]))
    else:
        state["status"] = "failed"
    if args.model_id:
        state.setdefault("model_runs", []).append(
            {
                "task": args.task_id,
                "model": args.model_id,
                "prompt_version": plan["prompt_version"],
                "status": args.status,
            }
        )
    remaining = [item for item in plan["tasks"] if item["status"] != "success"]
    if not remaining:
        state["stage"] = "extraction-completed"
        state["status"] = "completed"
    elif args.status == "success":
        state["stage"] = task["stage"]
        state["status"] = "running"
    save_state(project_dir, state)
    return {"task_id": args.task_id, "status": args.status, "artifact": artifact}


def plan_integrity_issues(plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    task_ids = [task["task_id"] for task in plan.get("tasks", [])]
    if len(task_ids) != len(set(task_ids)):
        issues.append("extraction plan task IDs must be unique")
    known = set(task_ids)
    graph = {task["task_id"]: set(task.get("dependencies", [])) for task in plan.get("tasks", [])}
    for task_id, dependencies in graph.items():
        unknown = sorted(dependencies - known)
        if unknown:
            issues.append(f"task {task_id} has unknown dependencies {unknown}")
        if task_id in dependencies:
            issues.append(f"task {task_id} depends on itself")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited or task_id not in graph:
            return
        if task_id in visiting:
            issues.append(f"dependency cycle detected at {task_id}")
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)
    return sorted(set(issues))


def load_quality_gate_module() -> Any:
    path = ROOT / "scripts" / "quality_gate.py"
    spec = importlib.util.spec_from_file_location("cangjie_quality_gate", path)
    if not spec or not spec.loader:
        raise CangjieError("could not load quality_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_project(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    paths = project_paths(project_dir)
    issues: list[str] = []
    bundles = 0
    project: dict[str, Any] | None = None
    try:
        project = load_project(project_dir)
        state = load_state(project_dir)
        if state["source_hash"] != project["source"]["source_hash"]:
            issues.append("pipeline-state source_hash does not match project.yaml")
        if state.get("project_id") and state["project_id"] != project["project_id"]:
            issues.append("pipeline-state project_id does not match project.yaml")
    except CangjieError as exc:
        issues.append(str(exc))

    if paths["plan"].is_file():
        try:
            plan = load_json(paths["plan"])
            validate_schema(plan, "extraction-plan.schema.json", str(paths["plan"]))
            issues.extend(plan_integrity_issues(plan))
            if project and plan["source_hash"] != project["source"]["source_hash"]:
                issues.append("extraction plan source_hash does not match project.yaml")
        except CangjieError as exc:
            issues.append(str(exc))

    quality_gate = load_quality_gate_module()
    skills_root = project_dir / "skills"
    if skills_root.is_dir():
        for skill_yaml in sorted(skills_root.glob("*/skill.yaml")):
            bundles += 1
            issues.extend(str(issue) for issue in quality_gate.validate_bundle(skill_yaml.parent))
    if issues:
        raise CangjieError("project validation failed:\n- " + "\n- ".join(issues))
    return {"project_dir": str(project_dir), "valid": True, "bundles": bundles, "issues": []}


def directory_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def export_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle_dir = args.bundle_dir.resolve()
    skill_path = bundle_dir / "skill.yaml"
    if not skill_path.is_file():
        raise CangjieError(f"missing skill.yaml: {skill_path}")
    skill = load_yaml(skill_path)
    skill_name = skill.get("name")
    if not skill_name:
        raise CangjieError("skill.yaml has no name")

    quality_gate = load_quality_gate_module()
    validation_issues = quality_gate.validate_bundle(bundle_dir)
    validated = not validation_issues
    if validation_issues and not args.allow_draft:
        raise CangjieError(
            "bundle failed quality gate; use --allow-draft only for review exports:\n- "
            + "\n- ".join(str(issue) for issue in validation_issues)
        )

    output = args.output.resolve()
    destination = {
        "generic": output / skill_name,
        "claude": output / ".claude" / "skills" / skill_name,
        "cursor": output / ".cursor" / "skills" / skill_name,
        "codex": output / "codex-skills" / skill_name,
    }[args.target]
    if destination.exists():
        if not args.force:
            raise CangjieError(f"export destination already exists: {destination}; use --force")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle_dir, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    note = {
        "generic": "Portable bundle; integrate it with the target agent runtime manually.",
        "claude": "Staged in a .claude/skills layout for review before installation.",
        "cursor": "Staged in a .cursor/skills layout for review before installation.",
        "codex": "Staged in a neutral codex-skills directory. Review INSTALL-CODEX.md; no product-specific global install path is assumed.",
    }[args.target]
    manifest = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "target": args.target,
        "skill": skill_name,
        "validated": validated,
        "source_bundle": str(bundle_dir),
        "destination": str(destination),
        "files": directory_hashes(destination),
        "note": note,
    }
    atomic_write_json(destination / "export-manifest.json", manifest)
    if args.target == "codex":
        atomic_write_text(
            output / "INSTALL-CODEX.md",
            "# Codex export staging\n\n"
            f"The `{skill_name}` bundle is staged at `codex-skills/{skill_name}/`.\n\n"
            "Review the bundle and reference its `SKILL.md` from the repository's agent instructions. "
            "This exporter intentionally does not assume a product-specific global installation path.\n",
        )
    return manifest


def project_status(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    project = load_project(project_dir)
    state = load_state(project_dir)
    summary: dict[str, Any] = {
        "project_id": project["project_id"],
        "mode": project["mode"],
        "stage": state["stage"],
        "status": state["status"],
        "completed_tasks": len(state["completed_tasks"]),
        "pending_tasks": len(state["pending_tasks"]),
        "cached_tasks": sum(1 for item in state.get("task_cache", {}).values() if item.get("status") == "success"),
        "artifacts": state["artifacts"],
    }
    plan_path = project_paths(project_dir)["plan"]
    if plan_path.is_file():
        plan = load_json(plan_path)
        summary["plan"] = {
            "chunks": len(plan.get("chunks", [])),
            "tasks": len(plan.get("tasks", [])),
            "success": sum(1 for task in plan.get("tasks", []) if task.get("status") == "success"),
            "pending": sum(1 for task in plan.get("tasks", []) if task.get("status") == "pending"),
            "failed": sum(1 for task in plan.get("tasks", []) if task.get("status") == "failed"),
        }
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="show mode profiles")
    profile_parser.add_argument("mode", choices=sorted(MODE_PROFILES), nargs="?")

    init_parser = subparsers.add_parser("init", help="initialize a Cangjie project")
    init_parser.add_argument("project_dir", type=Path)
    init_parser.add_argument("--project-id", required=True)
    init_parser.add_argument("--mode", choices=sorted(MODE_PROFILES), default="standard")
    init_parser.add_argument("--source-id", required=True)
    init_parser.add_argument("--source-title", required=True)
    init_parser.add_argument(
        "--source-type",
        choices=["book", "video", "podcast", "course", "interview", "article", "document-set"],
        required=True,
    )
    source_group = init_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-file", type=Path)
    source_group.add_argument("--source-hash")
    init_parser.add_argument("--author")
    init_parser.add_argument("--year", type=int)
    init_parser.add_argument("--goal", default="提炼可在真实任务中复用的方法论 Skills")
    init_parser.add_argument("--force", action="store_true")

    chunk_parser = subparsers.add_parser("chunk", help="create stable source chunks")
    chunk_parser.add_argument("project_dir", type=Path)
    chunk_parser.add_argument("--source-file", type=Path, required=True)
    chunk_parser.add_argument("--max-chars", type=int, default=12000)

    plan_parser = subparsers.add_parser("plan", help="build a deterministic Map-Reduce extraction plan")
    plan_parser.add_argument("project_dir", type=Path)
    plan_parser.add_argument("--chunks", type=Path)
    plan_parser.add_argument("--prompt-version", default="extractors-v1")
    plan_parser.add_argument("--model-id")

    record_parser = subparsers.add_parser("record-task", help="record a task result for resume/cache")
    record_parser.add_argument("project_dir", type=Path)
    record_parser.add_argument("--task-id", required=True)
    record_parser.add_argument("--status", choices=["success", "failed"], required=True)
    record_parser.add_argument("--artifact")
    record_parser.add_argument("--model-id")

    status_parser = subparsers.add_parser("status", help="show project and task status")
    status_parser.add_argument("project_dir", type=Path)

    validate_parser = subparsers.add_parser("validate", help="validate project, plan, state, and skill bundles")
    validate_parser.add_argument("project_dir", type=Path)

    export_parser = subparsers.add_parser("export", help="export a validated Skill bundle")
    export_parser.add_argument("bundle_dir", type=Path)
    export_parser.add_argument("--target", choices=["generic", "claude", "cursor", "codex"], required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--allow-draft", action="store_true")
    export_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            result = MODE_PROFILES[args.mode] if args.mode else MODE_PROFILES
        elif args.command == "init":
            result = init_project(args)
        elif args.command == "chunk":
            result = chunk_source(args)
        elif args.command == "plan":
            result = build_extraction_plan(args)
        elif args.command == "record-task":
            result = record_task(args)
        elif args.command == "status":
            result = project_status(args)
        elif args.command == "validate":
            result = validate_project(args)
        elif args.command == "export":
            result = export_bundle(args)
        else:
            parser.error(f"unknown command {args.command}")
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except CangjieError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

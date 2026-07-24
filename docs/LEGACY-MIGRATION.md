# Legacy Skill Migration

This guide migrates an existing Cangjie-style skill directory that contains a human-readable `SKILL.md` and possibly a legacy `test-prompts.json`.

## Safety model

The migrator is intentionally conservative:

- It requires a usable quote in `R — 原文`; it will not invent evidence.
- It writes `status: draft` and `verification.hard_gate_passed: false`.
- It does not create `test-results.json`, because test execution cannot be inferred from a Markdown claim.
- It does not overwrite files unless explicitly requested.

## Preview

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill
```

The command prints the proposed `skill.yaml`, `evidence.json`, normalized tests, warnings, and next steps without writing files.

## Write the scaffold

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill --write
```

The script writes:

- `skill.yaml`
- `evidence.json`
- `migration-report.json`
- `test-prompts.migrated.json` when a legacy test file is available

The original `test-prompts.json` is preserved. Review the migrated version and replace the original only after checking the cases.

Use `--force` only after reviewing the diff:

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill --write --force
```

With `--force`, the normalized tests replace `test-prompts.json`.

## Required review

1. Correct the source type and metadata.
2. Verify the quote and its chapter/page/timestamp.
3. Add independent evidence; a single migrated quote is not sufficient for publication.
4. Review routing, workflow, boundaries, and sibling priority.
5. Ensure the tests meet the minimum: 3 positive, 2 negative, 1 edge, with one explicit sibling-skill negative.
6. Execute the tests and create `test-results.json`.
7. Update verification scores, set `hard_gate_passed: true`, and set `status: tested` only after the evidence and tests genuinely pass.
8. Run:

```bash
python scripts/quality_gate.py path/to/legacy-skill
```

A freshly migrated bundle is expected to fail the final gate until review and test execution are complete.

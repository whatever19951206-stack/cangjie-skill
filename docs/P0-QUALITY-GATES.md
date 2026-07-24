# Cangjie P0 Quality Gates

## Goal

Move Cangjie from a prompt-only workflow toward an auditable skill compilation pipeline without removing the existing RIA-TV++ human-readable workflow.

## Delivered

- Machine-readable `skill.yaml`
- Structured `evidence.json`
- Executed `test-results.json`
- Machine `pipeline-state.json`
- JSON Schema for all machine artifacts
- Cross-file evidence reference and source consistency checks
- SHA-256 verification for evidence text
- Unresolved-placeholder detection
- Positive, negative, edge, and sibling-skill routing requirements
- Negative-test zero tolerance
- Summary/result consistency checks
- Unit tests and a complete passing example bundle
- GitHub Actions quality gate
- Main `SKILL.md` integration
- Conservative legacy migration tooling

## Evidence categories

- `direct_quote`
- `author_paraphrase`
- `inferred_method`
- `model_critique`
- `external_fact`

These categories must not be collapsed into a single “source-backed” label. In particular, model inference and critique must not be presented as an author's explicit claim.

## Commands

Install dependencies:

```bash
python -m pip install -r requirements-quality.txt
```

Validate one bundle:

```bash
python scripts/quality_gate.py path/to/skill
```

Validate all bundles and pipeline states:

```bash
python scripts/quality_gate.py --all
```

Run tests:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Migrate a legacy bundle conservatively:

```bash
python scripts/migrate_legacy_skill.py path/to/legacy-skill --write
```

## Publication rule

A skill must not be installed or published unless:

- its machine bundle is complete;
- evidence references and hashes validate;
- test results were actually executed;
- every negative test passes;
- the overall threshold passes;
- `verification.hard_gate_passed` is true;
- `status` is `tested` or `published`.

## Next: P1

P1 will evaluate whether a Skill produces measurable improvement rather than only checking internal consistency:

- separate routing, execution, and faithfulness evaluation;
- baseline vs Skill A/B comparison;
- hidden tests;
- sibling-skill confusion matrix;
- calculated uplift.

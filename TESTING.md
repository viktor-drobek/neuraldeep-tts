# Verification evidence

## Test-first development

On 2026-10-25 the documentation contract failed with missing SKILL.md and source files. The pure API suite failed because service_api.py did not exist. The orchestration regressions then reproduced missing-validator fallback, state creation before approval and existing-state exception handling. The fixes were implemented in the source before synchronization.

## Current verification

The full source suite passed on Python 3.14.4 in normal mode, C locale and encoding-warning mode. Each run reported 66 tests with one skip for a non-tag build. CLI help passed. Parsing every Python file with the Python 3.10 grammar also passed. No encoding warning was emitted. Tests use mocked transport and synthetic data only. Collection verification is recorded separately in the collection report.

## Commands

Run these with every NEURALDEEP credential removed and CODDY_HOME set to a nonexistent directory. TMPDIR must identify a private temporary directory outside the checkout.

```bash
python3 -B -m unittest discover -s tests
LC_ALL=C PYTHONUTF8=0 python3 -B -m unittest discover -s tests
python3 -X warn_default_encoding -B -m unittest discover -s tests
python3 -B scripts/client.py --help
git diff --check
```

No live authenticated quota check, billed operation or provider-success claim is part of this evidence. Python 3.10 syntax is checked locally and CI runs on Python 3.10 and the latest 3.x. No Codex /hooks approval was performed from this Coddy session.

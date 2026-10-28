# Verification evidence

## Test-first development

On 2026-10-25 the documentation contract failed with missing SKILL.md and source files. The pure API suite failed because service_api.py did not exist. The orchestration regressions then reproduced missing-validator fallback, state creation before approval and existing-state exception handling. The fixes were implemented in the source before synchronization.

## Initial verification (2026-10-25)

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

## README and agent-rule verification (2026-10-26)

Added the shared README contract and stronger native-rule checks before the documentation and configuration fixes. The initial runs failed on missing README sections and host details, missing fork matching and incomplete new-family workflow guidance. A further failing recovery-guide test caught obsolete claims about wallet transcription and SpeechCore uploads.

The full source suite passed on Python 3.14.4 in normal, C-locale and encoding-warning modes. Each mode ran **86 tests with 1 expected non-tag skip**. No EncodingWarning was emitted. The runner supplied PATH, HOME and TMPDIR in a cleared environment, a nonexistent CODDY_HOME and no NeuralDeep credentials. It invoked these commands from the source root.

```bash
python3 -B -m unittest discover -s tests
LC_ALL=C PYTHONUTF8=0 python3 -B -m unittest discover -s tests
python3 -X warn_default_encoding -B -m unittest discover -s tests
python3 -B scripts/client.py --help
git diff --check
```

The README CLI examples passed argument parsing without operation execution. Fenced Bash examples passed `bash -n`. New-family JSON examples passed the pure planner. All Python files parsed with the Python 3.10 grammar. This is a syntax check, not a local Python 3.10 runtime claim. The source workflow was included in the successful actionlint check of all 16 source and collection workflows.

Tests check the CLAUDE.md symlink, Cursor and Claude rule parity and scopes, Codex session and edit payloads, deduplication, fork resets and fail-open behavior. These are offline adapter checks, not confirmation of native host UI context loading. Interactive Codex `/hooks` approval was not performed. A collection-level native CLI probe was attempted but did not complete.

Guarded helper hashes are unchanged. No live skill API call, paid skill submission, global provider or MCP change, release or PR merge was performed for this documentation verification.

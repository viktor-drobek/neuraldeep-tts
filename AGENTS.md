# Source skill agent guide

This repository owns one self-contained NeuralDeep skill. Read `SKILL.md` and `STARTER_RELAY.md` for its public contract. The collection repository is a distribution mirror, not the source.

## Delivery workflow

Work on a feature branch through a source PR. Never push directly to main or merge without the owner's decision. Follow RED-GREEN-REFACTOR. Begin behavior changes with a failing offline test, make the smallest change pass, then refactor while tests stay green. Implement pure validation before transport, guards, state and CLI orchestration. Update contract documents together. Sync the collection only from committed source revisions.

`scripts/service_api.py` is pure validation. `scripts/client.py` is the only credential and operation entry point. `scripts/guarded.py` provides approved-origin transport and private atomic writes. Keep these three common files synchronized across the nine new skills, changing only the client's SERVICE line. Do not alter the six older helpers as a shortcut.

## Safety

Never inspect or print credentials, environment files or account data. Only the helper resolves keys. No live submissions during development. Unknown quotas and token billing fail closed. All new subscription submissions are blocked. Wallet approval is explicit and operation-specific. Never retry a POST or weaken a guard to pass a test. Preserve uncertain submission state for reconciliation.

## Verification

Tests use mocked HTTP. Run with credentials removed and a nonexistent CODDY_HOME. Use Python 3.10+ with the standard library only. Run unittest discovery, CLI help, locale and encoding-warning variants, then git diff --check. Record only commands actually run in TESTING.md.

## Rules Sync

AGENTS.md is the baseline and CLAUDE.md is its symlink. Cursor (`.cursor/rules/`), Coddy (`.coddy/rules/`) and Claude Code (`.claude/rules/`) rules have equivalent bodies and scopes. Keep always-on rules unconditional and translate scoped `globs` to the same `paths` without widening activation. Codex attaches Cursor rules through .codex/hooks.json and .codex/hooks/attach_rules.py. Run /hooks once per clone and after either hook changes. The hook fails open and is not a security boundary. Do not create a separate Codex rule index or copy rule bodies there.

## Releases

metadata.version in SKILL.md is the proposed semantic version. A release requires a matching vX.Y.Z tag after source review and merge. After owner approval, the release command is `gh release create vX.Y.Z --target main`. No release tag is created by this implementation. Use Viktor Drobek and viktor@drobek.online for both Git author and committer.

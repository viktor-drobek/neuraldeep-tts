# Delivery workflow

Follow RED-GREEN-REFACTOR. Start each observable change with a failing offline test. Implement from validation upward. Keep source contract documents aligned and sync the distribution only from committed source changes. No main pushes or PR merges without the owner's decision. Run the complete offline suite, CLI help and git diff --check before reporting success.

## Rules Sync

Keep `AGENTS.md` and its `CLAUDE.md` compatibility link aligned. Maintain equivalent bodies and activation scopes in `.cursor/rules/`, `.claude/rules/` and the existing `.coddy/rules/`. Cursor always-on rules use `alwaysApply: true` and Claude/Coddy omit `paths`. Scoped rules use `alwaysApply: false` with `globs` in Cursor and the same list as `paths` in Claude/Coddy. Never widen a scoped rule.

Codex reads Cursor rules through `.codex/hooks.json` and `.codex/hooks/attach_rules.py`. Run `/hooks` once per clone and after either hook file changes. The adapter fails open and is not a security boundary. Do not create a Codex rule index or copy rule bodies there. Keep the shared `tests/test_agent_rules.py` synchronized across source skills and run it after rule changes.

# Changelog

## 0.1.1 (unreleased)

- **Codex on Windows:** the hooks failed with "exit code 1", because Codex runs them in PowerShell 5.1, which rejects `||`. On Windows, `lineup init` now writes them as `cmd /c "lineup hook … || echo {}"`. Running `init` again replaces the 0.1.0 lines instead of adding a second set, and `init --remove` removes both forms. Claude Code hooks and Mac/Linux hooks are unchanged.
- **Junk files skipped:** files git ignores, plus caches and build output (`__pycache__/`, `*.pyc`, `*.pyo`, `node_modules/`, `.venv/`, `venv/`, `.pytest_cache/`, `.mypy_cache/`, `.DS_Store`) are no longer scored, shown, copied, sent to the AI or suggested for undo. `.env` and key files are still tracked even when git ignores them.
- **License:** `pyproject.toml` uses the SPDX `license = "MIT"` field.
- **Docs:** anything run with `!` inside the agent goes into its context, so the agent may comment on it. Use a second terminal to keep it quiet.

### Upgrading from 0.1.0

1. `pipx upgrade agent-lineup`
2. Run `lineup init` again in each repo (it replaces lineup's old hook lines).
3. Codex users: run `/hooks` in Codex to trust the changed hooks again.

# Changelog

## 0.1.1 (unreleased)

- **Codex on Windows:** the hooks failed with "exit code 1", because Codex runs them in PowerShell 5.1, which rejects `||`. On Windows, `lineup init` now writes them as `cmd /c "lineup hook … || echo {}"`. Running `init` again replaces the 0.1.0 lines instead of adding a second set, and `init --remove` removes both forms. Claude Code hooks and Mac/Linux hooks are unchanged.
- **Junk files skipped:** files git ignores, plus caches and build output (`__pycache__/`, `*.pyc`, `*.pyo`, `node_modules/`, `.venv/`, `venv/`, `.pytest_cache/`, `.mypy_cache/`, `.DS_Store`) are no longer scored, shown, copied, sent to the AI or suggested for undo. `.env` and key files are still tracked even when git ignores them.
- **License:** `pyproject.toml` uses the SPDX `license = "MIT"` field.
- **Usage log:** each repo keeps `.git/lineup/log.jsonl`: sessions, active days, rules fired by type and risk, file types, rollbacks (with time after the flag), command runs, files changed per reply, AI summary time, tokens and failures, hook errors, init and remove, and agent, OS, Python and lineup versions. No file names, paths, code, branch names or commit messages. Capped at about 5 MB. Logging never slows or breaks a hook or command. It replaces `usage.json`.
- **`lineup note "..."`:** saves a tester note as typed, with secrets replaced by `[REDACTED]`. `lineup note --list` shows them.
- **`lineup report --export`:** writes `lineup-notes.md` in the repo root (summary, usage tables, notes), kept out of git and skipped by lineup. It starts with a reminder to read it before sending.
- **`lineup init --remove`:** after removing the hooks, offers to export your notes (Enter = yes), then to delete `.git/lineup/` (Enter = keep). Piped or with `--yes`, it does neither.
- **Rollback messages** keep file names in their exact case (it printed "Auth.cpython-310.pyc").
- **Docs:** anything run with `!` inside the agent goes into its context, so the agent may comment on it. Use a second terminal to keep it quiet.

### Upgrading from 0.1.0

1. `pipx upgrade agent-lineup`
2. Run `lineup init` again in each repo (it replaces lineup's old hook lines).
3. Codex users: run `/hooks` in Codex to trust the changed hooks again.

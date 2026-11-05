# agent-lineup

`lineup` runs after a coding agent (Claude Code or Codex) makes changes. It tells you what changed, which edits are risky, and lets you undo a file.

- `lineup diff` finds the changes
- `lineup risky` scores and flags them
- `lineup rollback` undoes a file
- `lineup init` installs the Claude Code and Codex hooks (Codex: trust them with /hooks)

Before anything is sent to your AI agent, lineup leaves out private files and hides secret-looking text. The secret scan can miss things, so don't rely on it alone.

Work in progress. See [docs/plan.md](docs/plan.md).

## Develop

```
pip install -e ".[dev]"
pytest
```

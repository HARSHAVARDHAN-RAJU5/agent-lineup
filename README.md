<p align="center"><img src="assets/lineup-logo.svg" alt="lineup logo" width="324"></p>

# agent-lineup

`lineup` runs after a coding agent (Claude Code or Codex) makes changes. It tells you what changed, which edits are risky, and lets you undo a file.

- `lineup diff` finds the changes
- `lineup risky` scores and flags them
- `lineup rollback` undoes a file
- `lineup init` installs the Claude Code and Codex hooks (Codex: trust them with /hooks)

Before anything is sent to your AI agent, lineup leaves out private files and hides secret-looking text. The secret scan can miss things, so don't rely on it alone.

## Where to run it

Run lineup with `!` in the same session (for example `! lineup risky`), or in a second terminal in the same folder.

The agent may still comment on `!` output. Its commentary isn't part of lineup's check: lineup's scores come from fixed rules, not from the agent. A second terminal avoids this.

Work in progress. See [docs/plan.md](docs/plan.md).

## Develop

```
pip install -e ".[dev]"
pytest
```

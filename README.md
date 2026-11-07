<p align="center"><img src="assets/lineup-logo.svg" alt="lineup logo" width="324"></p>

# lineup

`lineup` shows what your coding agent (Claude Code or Codex) just changed, flags the risky edits, and lets you undo a file.

## Demo

After the agent's reply, the hook prints a short line:

```
⚠ ≈ lineup: 2 HIGH
  2 auth.py        auth or payment file changed
  3 test_utils.py  assertion removed
Undo: lineup rollback 2
```

`lineup risky` shows every changed file and the rule behind its score:

```
| ID | File          | Risk | Why (rule)                                  | Other           |
| 1  | .env          | HIGH | .env or key file changed, contents not read | Can't roll back |
| 2  | auth.py       | HIGH | auth or payment file changed                |                 |
| 3  | test_utils.py | HIGH | assertion removed                           |                 |
| 4  | utils.py      | LOW  | code edited, + function added: mul          |                 |
```

`lineup rollback 2` (or `lineup rollback auth.py`) puts the file back:

```
2   auth.py  restored
Your versions from before this rollback are saved in .git/lineup/backups/2026-11-06_004042/.
Also changed: .env (ID 1), test_utils.py (ID 3), utils.py (ID 4). Check whether they depend on this file.
```

## Install

Needs Python 3.10 or newer, git, and [pipx](https://pipx.pypa.io).

```
pipx install agent-lineup
cd your-repo
lineup init --agent claude
```

Use `--agent codex` if you use Codex, or leave `--agent` out for both.

`lineup init` shows the hooks it will add and asks before writing. Then start a new Claude Code session. In Codex, run `/hooks` and trust the lineup hooks.

- No pipx: `python -m pip install --user pipx`, then `python -m pipx ensurepath`, then open a new terminal.
- No git: lineup says "git isn't installed or isn't on your PATH". Install git first.
- Upgrade: `pipx upgrade agent-lineup`.
- Uninstall: run `lineup init --remove` in each repo first, then `pipx uninstall agent-lineup`. To delete lineup's saved data too, delete the `.git/lineup` folder in each repo.

## Commands

| Command | What it does |
|---|---|
| `lineup init` | Adds the hooks for Claude Code (`.claude/settings.local.json`) and Codex (`.codex/hooks.json`). `--agent claude` or `--agent codex` picks one. `--remove` takes them out again. |
| `lineup diff` | What changed since the session started, with a short AI summary. |
| `lineup risky` | Scores each changed file HIGH, MED or LOW using fixed rules, and shows the rule. |
| `lineup rollback 3 4` | Undoes files by ID or name. Your current version is backed up first. |
| `lineup report` | Usage counts to send as tester feedback. No code or file names. |

## What gets sent to the AI

Only `lineup diff` uses AI. It sends the changed lines to the agent you already have (Claude Code with Haiku, or Codex), with no tools. Nothing goes anywhere else, and lineup needs no API key.

- `.env`, key and credential files are never sent. They're listed by name only.
- Lockfile contents are left out. Secret-looking text is hidden before sending.
- The hooks, `risky`, `rollback` and `report` use no AI. They run only on your machine.
- lineup keeps its photo, file copies and backups in `.git/lineup/` in your repo. Private files are kept as fingerprints only, never copied.

## Run it in a second terminal

You can run lineup with `!` in the same session (for example `! lineup risky`), but the agent may comment on the output. Its commentary isn't part of lineup's check: the scores come from fixed rules, not from the agent.

To avoid this, open a second terminal in the same folder and run lineup there.

## Known limits

- **Claude Code is tested. Codex hooks aren't fully tested yet**, and you must trust them with `/hooks` before they run.
- The secret scan can miss things. Don't rely on it alone.
- Tested on Windows. Mac and Linux aren't tested yet.
- Rollback can't undo private files, files over 10 MB, links, or changes that are already committed.
- Scores come from simple rules, so expect some false flags.

## Develop

```
pip install -e ".[dev]"
pytest
```

Testers: [docs/testers.md](https://github.com/HARSHAVARDHAN-RAJU5/agent-lineup/blob/main/docs/testers.md).

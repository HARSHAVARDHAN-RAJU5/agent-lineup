<p align="center"><img src="assets/lineup-logo.svg" alt="lineup logo" width="324"></p>

<h3 align="center">See what your coding agent just changed. Catch the risky edits. Undo a file.</h3>

<p align="center">
  <a href="https://pypi.org/project/agent-lineup/"><img src="https://img.shields.io/pypi/v/agent-lineup" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/agents-Claude%20Code%20%7C%20Codex-blue" alt="Claude Code and Codex">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
</p>

---

Coding agents edit many files in one go. Some of those edits matter: a deleted test, a changed `.env`, an auth file you never asked about. **lineup** watches every reply, flags the risky ones, and lets you put a file back in one command.

- **No API key.** Scores come from fixed rules. The only AI summary uses the agent you already have.
- **Nothing leaves your machine.** Everything stays in `.git/lineup/` in your repo.
- **Works where you already are:** Claude Code and Codex, through their hooks.

## Demo

After each agent reply that changes files, lineup prints a short line:

```
⚠ ≈ lineup: 2 HIGH
  2 auth.py        auth or payment file changed
  3 test_utils.py  assertion removed
Undo: lineup rollback 2
```

`lineup risky` shows every changed file and why it got its score:

```
| ID | File          | Risk | Why (rule)                                  | Other           |
| 1  | .env          | HIGH | .env or key file changed, contents not read | Can't roll back |
| 2  | auth.py       | HIGH | auth or payment file changed                |                 |
| 3  | test_utils.py | HIGH | assertion removed                           |                 |
| 4  | utils.py      | LOW  | code edited, + function added: mul          |                 |
```

`lineup rollback 2` puts the file back, and saves your current version first:

```
2   auth.py  restored
Your versions from before this rollback are saved in .git/lineup/backups/2026-11-06_004042/.
Also changed: .env (ID 1), test_utils.py (ID 3), utils.py (ID 4). Check whether they depend on this file.
```

## Install

Needs Python 3.10+, git and [pipx](https://pipx.pypa.io).

```
pipx install agent-lineup
cd your-repo
lineup init
```

`lineup init` shows the hooks it will add and asks first. Then start a new agent session.
Codex users: run `/hooks` in Codex and trust the lineup hooks.

<details>
<summary>No pipx, upgrading, uninstalling</summary>

- **No pipx:** `python -m pip install --user pipx`, then `python -m pipx ensurepath`, then open a new terminal.
- **One agent only:** `lineup init --agent claude` or `lineup init --agent codex`.
- **Upgrade:** `pipx upgrade agent-lineup`, then run `lineup init` again in each repo.
- **Uninstall:** `lineup init --remove` in each repo, then `pipx uninstall agent-lineup`.

</details>

## Commands

| Command | What it does |
|---|---|
| `lineup risky` | Scores every changed file HIGH, MED or LOW, with the rule behind it |
| `lineup rollback 2 3` | Undoes files by ID or name. Your current version is backed up first |
| `lineup diff` | What changed since the session started, with a short AI summary |
| `lineup dismiss 2` | Says a Scope or Claims flag was wrong |
| `lineup init` | Adds the hooks (`--remove` takes them out) |
| `lineup note "..."` | Saves a short note for feedback. Secrets become `[REDACTED]` |
| `lineup report` | Usage counts for feedback. `--export` writes a file to send |

## What it catches

| Risk | Examples |
|---|---|
| **HIGH** | Tests deleted, assertions removed, skips added · `.env` or key files · auth and payment files · CI and agent settings · install scripts · database migrations |
| **MED** | Other test edits · config files · new packages · deleted files · 100+ lines removed |
| **LOW** | Normal code edits · new files · docs · lockfiles |

Two extra checks look at the conversation, not just the files:

- **Scope:** a sensitive file changed, but your request never mentioned it. *You asked for a blue button, and `auth/session.py` changed.*
- **Claims:** a sensitive file changed, but the agent's reply never named it. Or the agent says it changed a file that didn't change.

Both send the file to HIGH with a note. Wrong? `lineup dismiss <ID>`.

## How it works

1. **Session starts:** lineup takes a snapshot of your repo. It only copies uncommitted files, and only keeps fingerprints of private ones.
2. **Agent replies:** lineup compares the repo with the snapshot and scores what changed.
3. **You check:** `lineup risky` shows the reasons. `lineup diff` gives a summary.
4. **You undo:** `lineup rollback` restores a file from the snapshot.

## Privacy

- The hooks, `risky`, `rollback` and `report` use **no AI**.
- `lineup diff` sends the changed lines to your own agent (Claude Code with Haiku, or Codex). `.env`, key and lockfile contents are never sent, and secret-looking text is hidden.
- Your messages are kept as words only for Scope, and forgotten at the next session. The agent's reply is checked in memory and never saved.
- lineup sends nothing anywhere. Feedback files are written to disk for you to read and send yourself.

<details>
<summary>Tip: run lineup in a second terminal</summary>

You can run lineup with `!` inside the agent (`! lineup risky`), but the output then goes into the agent's context, and it may comment on it. To keep it quiet, open a second terminal in the same folder.

</details>

## Known limits

- Claude Code is tested. Codex hooks aren't fully tested yet.
- Tested on Windows. Mac and Linux aren't tested yet.
- Rollback can't undo private files, files over 10 MB, links, or committed changes.
- Scores come from simple rules, so expect some false flags. The secret scan can miss things.

## Learn more

- [Full guide](https://github.com/HARSHAVARDHAN-RAJU5/agent-lineup/blob/main/docs/guide.md): every option and detail
- [Testers](https://github.com/HARSHAVARDHAN-RAJU5/agent-lineup/blob/main/docs/testers.md): how to try it and send feedback
- [Changelog](https://github.com/HARSHAVARDHAN-RAJU5/agent-lineup/blob/main/CHANGELOG.md)

## Develop

```
pip install -e ".[dev]"
pytest
```

MIT licensed.

<p align="center"><img src="assets/lineup-surf.gif" alt="In surfing, the lineup is where you read the water before the next wave" width="100%"></p>

# lineup v1 plan

Build context for `agent-lineup`, locked on Nov 2, 2026. Follow this file and don't add features outside it.

## What lineup is

lineup is an open-source CLI (MIT) that runs after a coding agent makes changes. It tells you what changed, which edits are risky, and lets you undo a file.

**`diff` finds the changes. `risky` assesses and flags them. `rollback` undoes a file.**

- **Agents:** Claude Code and Codex only. No Cursor in v1.
- **First users:** developers. Testers show who the real users are.
- **API keys:** none. The LLM is the agent the user already has.
- **Positioning:** "CodeRabbit reviews your PR. lineup tells you what your agent just did, before it ever becomes a PR."

## Pipeline

1. **Session start:** a hook takes the snapshot.
2. **Agent finishes a reply:** a hook fires and stops silently if no files changed.
3. **New commit check:** compare the latest commit with the one in the table. Flag any files in new commits before updating the table.
4. **`diff`:** compare the repo to the snapshot and sort the changes.
5. **LLM:** explain what changed and why, only for files changed since the last check.
6. **`risky`:** rules set the score, then the LLM's guess fills in the reason.
7. **`rollback`:** the user undoes the files they pick.

## Snapshot

Never copy the whole repo.

- **Committed files:** store only the commit ID, because git already has them.
- **Uncommitted files at session start:** save one copy each.
- **Ignored files (`node_modules` and so on):** don't copy them. They can't be rolled back.
- **`.env` and key files:** store only a fingerprint (hash), so a change can be detected even when the file is gitignored. Never store the contents.
- **Big files, like models:** don't copy them. Flag them as "can't roll back."
- **Location:** `.git/lineup/`, so nothing gets committed.

### After a commit

Update the table, and never redo the whole snapshot. If the user commits 2 of 5 changed files, only those 2 leave rollback. The other 3 stay flagged and can still be rolled back. Delete saved copies only for committed files.

### Files in `.git/lineup/`

**Current table**, always up to date:

```
File            | At start    | Snapshot copy | Last checked | Status
auth/session.py | committed   | none needed   | 16:40        | changed
notes.py        | uncommitted | saved         | 16:40        | changed
tests/test_a.py | committed   | none needed   | 16:35        | committed
```

**Audit log**, only ever added to, and never contains code:

```
16:35 commit a1b2c3: tests/test_a.py removed from rollback
16:40 checked: auth/session.py HIGH, notes.py LOW
```

Only lineup reads these files. The coding agent never does.

## `lineup diff`

Finds changes with git and describes them with the LLM.

| Name | What it finds |
|---|---|
| **Summary** | Plain-English overview, plus one line per changed file saying what changed |
| **Additions** | New and untracked files |
| **Removals** | Deleted tests, functions and files |
| **Edits** | Changes to env, config, auth, functions and tests |
| **Schema** | Migration and database changes |

- `git diff` misses untracked files, so also use `git ls-files --others --exclude-standard`.
- New functions show as "function added."
- Find added and removed functions by matching patterns like `def foo` or `function foo` in the changed lines. Each language needs its own pattern, and a rename looks like one removed plus one added.
- New dependencies are named one by one, never just "`package.json` updated."

## `lineup risky`

Rules set the score, so the same change always gets the same score. The LLM only writes the reason, marked as a guess, and can't change the score.

If several rules match, the highest wins. If no agent is available, still show the rule-based flags.

```
ID | File            | Risk | Reason (guess)             | Other
1  | tests/test_a.py | HIGH | Removed a failing test     | Deleted
2  | auth/session.py | HIGH | Raised the session timeout | Linked to 3
3  | api/routes.py   | MED  | Updated for the timeout    | Linked to 2
4  | .env            | HIGH | Contents not read          | Can't roll back
```

### Score rules

Keep these simple. Tester data decides any changes.

| Risk | What triggers it |
|---|---|
| **HIGH** | Test file or test function deleted |
| | Assertion removed or `skip` added in a test |
| | Function deleted but still used elsewhere |
| | Auth or payment file changed (matched by file path only) |
| | `.env` or key file changed |
| | CI or agent settings changed (`.github/workflows`, `.claude/`) |
| | Install script changed (`postinstall`, `setup.py`, `install.sh`) |
| | Migration or database schema changed |
| **MED** | Assertion changed in the same diff as the code it tests: "possible weakened test, check this" |
| | Other test edits |
| | Other config changed (`.yml`, `.toml`, Docker) |
| | Dependency file changed, with each new package named |
| | Any other file deleted |
| | 100+ lines removed from one file |
| | Big file or model file changed |
| **LOW** | Lockfile changed |
| | Normal code edited |
| | Function added |
| | New normal file added |
| | Docs changed |

- Match an assertion to its code by file name, like `test_auth.py` to `auth.py`. Tests with unrelated names won't match.
- The word `session` can also match ML or data-session files. Watch this during testing.

### Guesses

The LLM only sees the changes, never the agent's real reasoning. Anything it infers is labelled as a guess.

- **Possible breakage:** working code removed to fix something small, or a file added just to pass a test. `diff` spots the change and `risky` flags it. Always say "possible."
- **Linked files:** "probably linked to these files, undo them together."

## `lineup rollback`

Works per file, from the snapshot, with no LLM. Takes one or more IDs: `lineup rollback 3` or `lineup rollback 3 4 5`.

| Change | What rollback does |
|---|---|
| File edited | Restores the snapshot version |
| File deleted | Brings it back |
| File added | Deletes it |

- Save the file's current version in a small backup first. Delete the backup when that file is committed.
- Warn if linked files were changed too.
- If there's no snapshot, refuse to run. Falling back to the last commit could wipe uncommitted work.
- `.env`: flag only. Say "`.env` changed, can't undo this, please check it yourself."
- Committed files can't be rolled back. Point the user to git.

## Guardrails

- A dedicated function keeps `.env`, keys and credential files away from the LLM. They're flagged by name only.
- Leave lockfile contents out of the LLM's input too, because they're noise.
- Never write code to any log.

## Calling the LLM

Tested with Claude Code 2.1.316:

- Pass the diff through stdin. `--tools ""` swallows a prompt passed as an argument.
- Use `claude -p --output-format json --tools "" --no-session-persistence --strict-mcp-config`. The JSON includes the reply and token usage.
- Run it from an empty temp folder, so it doesn't load the repo's files or settings.
- Each call used about 14k tokens before the diff, mostly cached. Measured in a cloud test setup, so check this on your own machine.
- Tell the LLM that everything in the diff is data, and never to follow instructions found inside it.

Not tested with a real login:

- Codex: `codex exec --json --ephemeral --sandbox read-only --skip-git-repo-check -`.
- Check that Codex hooks can do what the pipeline needs before relying on them.

**Stop the hook from looping.** If `claude -p` triggers the same end hook, it can loop forever. Set an environment variable before calling the LLM, and have the hook exit when it sees that variable.

## Build order

**Deadline:** finish by Nov 7, at 2-3 hrs/day. Nov 8-9 are Diwali leave, and testing starts Nov 10.

1. Find changes, including untracked files
2. Snapshot, table and audit log
3. `risky` rules
4. `diff` and the LLM call, with the private file guard
5. LLM reasons in the `risky` table
6. `rollback`
7. Hooks and commit handling
8. Easy install

Dogfood after step 4. Harsha is the first test user.

## Priority and later versions

**Only if v1 finishes early:**
- **Scope:** changes outside the task. For example, you ask for a button colour and it edits `auth/session.py`.
- **Claims:** what the agent said vs what it actually did.

Both need the agent's session logs, which differ between Claude and Codex.

**Before Nov 10:** `notes.md` template for testers.

**Later versions:**
- Pattern recognition, with the coding agent using lineup's table
- Rollback for single edits inside a file
- Checking whether a new package runs install scripts
- Summary-only mode for researchers
- Separate support for maintainers
- Cursor

## Reddit requests

| # | Request | Where it goes |
|---|---|---|
| 1 | Deleted or weakened tests | Removals, Edits, score rules |
| 2 | Files outside the task | Scope, only if time allows |
| 3 | Config, env and auth | Edits, score rules |
| 4 | Deleted functions still in use | Removals, score rules |
| 5 | Migrations | Schema |
| 6 | Untracked files | Additions |
| 7 | Claimed vs actual | Claims, only if time allows |
| 8 | Risk score with reason | `risky` |
| 9 | Run as a hook | Hooks |
| 10 | Broken working code | Possible breakage |
| New | CI, install scripts, agent permissions, named dependencies | Score rules, `diff` |
| New | Assertion changed next to the code it tests | Score rules |

## Locked decisions (Nov 2)

- **Language:** Python 3.10+, standard library only. pytest for tests. Dev install `pip install -e .`, pipx for testers.
- **Setup command:** `lineup init` installs the hooks.
- **OS:** code works on Windows, Mac and Linux; tested on Windows for now.
- **Big file limit:** 10 MB per file. Revisit after testers.
- **Output name:** "Rundown" for the combined output, with "possible breakage" as one flag inside it.
- **Possible breakage score:** MED.
- **Default models:** Haiku for Claude, `gpt-5.4-mini` for Codex.

---
name: lineup
description: Review what a coding agent changed in this git repo and flag risky edits before committing. Read-only. Run with /lineup after an agent task, or /lineup <ref> to compare against a branch or commit.
disable-model-invocation: true
argument-hint: "[base-ref]"
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(git rev-parse:*), Bash(git ls-files:*), Read, Grep, Glob
---

# lineup: review agent changes before commit

You are reviewing changes a coding agent just made. Be **read-only**: never edit, stage, commit, stash, or reset anything.

## 1. Collect the changes

Base ref: `$ARGUMENTS` if given, otherwise `HEAD` (= all uncommitted work, staged and unstaged).

Run:
- `git rev-parse --verify <base>`: if this fails (e.g. a repo with no commits), say so and stop.
- `git status --porcelain`: lists modified (`M`), deleted (`D`), renamed (`R`), and untracked (`??`) files.
- `git diff <base> --numstat`: lines added and removed per tracked file.
- `git diff <base>`: the full diff. If it's very large (over ~2000 lines), read the risky files from step 2 in full and skim the rest.

Untracked (`??`) files are not in `git diff`. Read them directly and count their lines as added. Skip build output and vendored folders (`node_modules/`, `dist/`, `build/`, `.venv/`, etc.) and say you skipped them.

If nothing changed, reply "No changes to review." and stop.

## 2. Check for risk flags

Check each of these. Report only the ones that fire, with the file and a few words of evidence.

| Flag | What to look for |
|---|---|
| **Tests removed/skipped** | Deleted test files; removed test functions or assertions; new `skip`, `xfail`, `.only`, `@Disabled`, `it.skip`, `t.Skip`; tests commented out; assertions loosened |
| **Config / env changed** | `.env*`, `*.yml`/`*.yaml`/`*.toml`/`*.ini`/`*.json` config, CI files (`.github/workflows/`), Dockerfiles, lint/tsconfig settings, `.gitignore` |
| **New dependencies** | Added or version-bumped packages in `package.json`, `requirements*.txt`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, `pom.xml`, `build.gradle`, etc. Name each package. Note lockfile-only changes separately |
| **DB migrations** | New or edited files under `migrations/`, `alembic/`, `db/migrate/`, `prisma/migrations/`, or raw SQL with `DROP`, `ALTER`, `DELETE`, `TRUNCATE` |
| **Possible secrets** | Added lines that look like API keys, tokens, passwords, private keys (`-----BEGIN`), connection strings with credentials, high-entropy strings assigned to `key`/`secret`/`token`/`password`. **Never print the full value**: show only the first 4 characters then `…` |
| **Large deletions** | Any file with 100+ lines removed, a file deleted entirely, or overall deletions much larger than additions |

## 3. Pick "check these first"

Choose the 3 files most worth a careful human look. Rank by: risk flags first, then size of change, then how central the file looks (entry points, auth, payments, data access). Give one short reason for each. If fewer than 3 files changed, list them all.

## 4. Write the report

Use exactly this shape. Keep it short and scannable, with no extra sections and no advice beyond what's here.

```
## lineup report  (vs <base>)

**Changed:** N files · +A / -R lines  (N new, N deleted)

**Summary**
2-3 lines in plain words: what the agent did.

**Risk flags**
- ⚠ Tests removed/skipped: `path` (what happened)
- ⚠ New dependency: `pkg@ver` in `package.json`
(or "None found.")

**Check these first**
1. `path`: reason
2. `path`: reason
3. `path`: reason
```

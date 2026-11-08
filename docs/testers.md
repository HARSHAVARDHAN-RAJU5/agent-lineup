# Testing lineup

Thanks for testing. This takes about 10 minutes to set up, then a minute a day.

## What lineup is

lineup checks what your coding agent (Claude Code or Codex) just changed. After each reply it flags risky edits, like a changed auth file or a removed test assertion. You can undo a file with `lineup rollback`.

## Before you start

**Check your company's rules before using lineup on work code.** `lineup diff` sends changed lines to your AI agent. If you can't use it on work code, use a personal project.

What leaves your machine:
- Only `lineup diff` uses AI. It sends the changed lines to the agent you already use (Claude Code or Codex), the same way the agent itself would.
- `.env`, key and credential files are never sent. Secret-looking text is hidden, but the scan can miss things.

What stays on your machine:
- Everything else: the hooks, `lineup risky`, `lineup rollback` and `lineup report`.
- lineup's data lives in `.git/lineup/` inside your repo. Nothing is uploaded, and lineup never sends us anything.

## Install

You need Python 3.10 or newer and git. Then:

```
python -m pip install --user pipx
python -m pipx ensurepath
```

Open a new terminal, then:

```
pipx install agent-lineup
cd your-repo
lineup init --agent claude
```

Use `--agent codex` instead if you use Codex, so you only get files for the agent you use. `lineup init` shows what it will add and asks first. Type `y`. Then start a new Claude Code session in that repo. In Codex, run `/hooks` and trust the lineup hooks.

## Use it for 2 weeks

1. Work with your agent as usual. After each reply, lineup prints a line if anything changed.
2. When it shows HIGH, open a second terminal in the same folder and run `lineup risky` to see why. Use a second terminal rather than `!` inside the agent: anything run with `!` goes into the agent's context, so it may comment on it.
3. To undo a file: `lineup rollback 3` (the ID) or `lineup rollback auth.py` (the name).
4. Run `lineup diff` for an AI summary of what changed.

## What to log

Copy [notes-template.md](https://github.com/HARSHAVARDHAN-RAJU5/agent-lineup/blob/main/docs/notes-template.md) somewhere private and add a short entry when lineup helps, gets something wrong, or annoys you. Don't paste code, secrets or company details.

## Sending feedback

- Harsha will message you the check-in questions each week.
- At the end, run `lineup report` in each repo you used. It prints counts only (runs, flags by risk level, rollbacks, days active), with no code or file names. Read it, then paste it with your notes into the final form.

## Removing lineup

In each repo you used:

```
lineup init --remove
```

Then `pipx uninstall agent-lineup`. To delete lineup's saved data as well, delete the `.git/lineup` folder in each repo.

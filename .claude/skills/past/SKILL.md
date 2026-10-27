---
name: past
description: End-of-session wrap-up. Records this session and the next-session plan in .claude/sessions.md (keeps only the last 3 sessions) and updates Working preferences in ~/.claude/CLAUDE.md. Run with /past at the end of a session.
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash(git log:*), Bash(git status:*), Bash(git diff:*)
---

# past: wrap up this session

Goal: the next session can continue without me re-explaining anything, and both files stay small.

## 1. Review the whole session

Go through the entire conversation and `git log`/`git status`. Note:
- **Done:** what was built or changed (files, commits). Results, not every step.
- **Decisions:** choices made and why, in one line each.
- **Open:** unfinished work, bugs, open questions.
- **Learned:** things the user corrected, preferred, or rejected, and what worked or didn't.

## 2. Draft `.claude/sessions.md`

Read it if it exists. Build the new version in exactly this shape:

```
# Session history

## Next session plan
- (2-5 concrete next steps, most important first)

## Sessions (newest first, max 3)

### YYYY-MM-DD: short title
- Done: ...
- Decisions: ...
- Open: ...
```

Rules:
- Put this session at the top. Keep at most 3 sessions in total and delete the oldest.
- Replace "Next session plan" completely. Never append to the old one.
- Max ~6 bullets per session. Drop anything the code or git history already shows.

## 3. Draft preference updates (~/.claude/CLAUDE.md)

Update the `# Working preferences` section in `~/.claude/CLAUDE.md` (user-level, not the project file).
- Only save a preference if the user **corrected you** or **stated it clearly**. Not from one passing comment.
- If a rule already covers it, **edit that rule**. Don't add a near-duplicate.
- Remove rules that are now wrong.
- Max 10 rules. If full, merge or drop the weakest.
- Don't touch anything else in that file.

Separately, if the project's scope or tech changed, fix `## Project status` or `## Project rules` in the project CLAUDE.md.

If nothing qualifies, say "No preference changes".

## 4. Show, wait, then save

Show the user, as separate sections:
1. **Diary:** the full new `sessions.md`
2. **Preferences** (~/.claude/CLAUDE.md): each rule marked **NEW**, **CHANGED** (old → new), or **REMOVED**
3. **Project CLAUDE.md:** exact changes, if any

Wait for their OK and apply any adjustments. Only then write the files. Don't commit unless asked.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

agent-lineup reviews what a coding agent changed and flags risky edits before commit.
- Stage 1 (now): Claude Code skills only, no code yet.
  - `/lineup` in `.claude/skills/lineup/` reviews uncommitted changes and gives a risk report.
  - `/past` in `.claude/skills/past/` is the end-of-session wrap-up.
- Stage 2 (later, not yet): a Python CLI `lineup` (`start` / `report`), rule-based, built from what Stage 1 shows matters. Don't write Python or CLI code until asked.

## Session continuity

- At the start of a session, read the session history below and continue from "Next session plan" unless the user says otherwise.
- At the end of a session the user runs `/past`. It updates the session history (max 3 sessions) and the Working preferences in ~/.claude/CLAUDE.md.

@.claude/sessions.md

## Project rules
- Keep it simple: one file per skill, no scripts, unless asked.

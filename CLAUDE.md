# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

agent-lineup is a Python CLI, `lineup`, that runs after a coding agent (Claude Code or Codex) makes changes.
`diff` finds the changes, `risky` scores and flags them, `rollback` undoes a file.
- v1 spec: `docs/plan.md`. Follow it and don't add features outside it.
- Deadline: v1 done by Nov 7, 2026; testers start Nov 10.

## Commands
- Install for development: `pip install -e ".[dev]"`
- Run tests: `pytest`

## Project rules
- Python 3.10+, standard library only at runtime. pytest for tests.
- Code must work on Windows, Mac and Linux (no OS-specific paths or shell tricks).
- Build in phases (see `docs/plan.md` build order). After each phase: tests pass, show Harsha, commit each small change after OK, then push.
- Never send `.env`, key or credential file contents to the LLM, and never write code into any log.

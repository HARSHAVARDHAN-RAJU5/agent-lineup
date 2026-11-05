"""What lineup does when Claude Code or Codex calls it from a hook, and `lineup init`, which installs the hooks.

Hooks never block the agent and never call the AI. Each prints JSON both tools understand:
- "systemMessage": a line shown to the user
- "hookSpecificOutput.additionalContext": a note for the agent
"""

import json
import os
import sys
from pathlib import Path

from lineup import changes, git, hookjson, llm, rules, snapshot, store

# ---- reading the hook's input ----

def read_event(stream=None):
    """The JSON the agent sends on stdin, or {} when run by hand (or on bad input)."""
    stream = stream or sys.stdin
    try:
        if stream is None or stream.isatty():
            return {}
        text = stream.read()
    except (OSError, ValueError, AttributeError):
        return {}
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def emit(message=None, context=None, event=None):
    """Print the hook's answer as JSON (Codex's Stop hook requires JSON, so even silence is "{}")."""
    out = {}
    if message:
        out["systemMessage"] = message
    if context and event:
        out["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": context}
    print(json.dumps(out))


def repo_root(event):
    """The repo the hook is about: the agent's working folder from the event, else the current folder."""
    try:
        return git.repo_root(event.get("cwd") or ".")
    except git.GitError:
        return None


def loop_guard_on():
    """True while lineup's own AI call is running, so hooks it triggers do nothing."""
    return os.environ.get(llm.LOOP_GUARD) == "1"


# ---- the note for the agent after a rollback ----

def rollback_note(root):
    """The note for the agent about files the user rolled back, delivered once. None if there's none."""
    rolled = store.pop_rollback_note(root)
    if not rolled:
        return None
    return f"Rolled back by the user: {', '.join(rolled)}. Read it again before editing." if len(rolled) == 1 \
        else f"Rolled back by the user: {', '.join(rolled)}. Read them again before editing."


# ---- the three hooks ----

def on_session_start(root, event, agent):
    """Session start (startup, resume, clear, compact or fork): take or keep the photo.

    fork is treated like resume: the forked session continues the same work, so the same photo applies.
    """
    note = rollback_note(root)  # read before a fresh photo could replace the table
    table, kept = snapshot.start_session(root, agent=agent)
    if kept:
        word = "file" if kept == 1 else "files"
        message = (f"lineup: keeping the earlier snapshot, {kept} uncommitted {word} from before this session. "
                   "Some may be your own edits. Commit them, or run lineup hook start --fresh to start over.")
    else:
        label = "no commits yet" if table["start_commit"] == git.EMPTY_TREE else table["start_commit"][:7]
        message = f"lineup: photo taken at {label} ({snapshot.summary(table)})"
    emit(message, note, "SessionStart")


def on_stop(root, event):
    """End of the agent's reply: one fast rule-based line about what this reply changed, or nothing."""
    if event.get("stop_hook_active") or not store.has_snapshot(root):
        emit()
        return
    before = store.load_table(root)
    table = store.load_table(root)
    committed = snapshot.check_commits(root, table)  # flag new commits first, then off the undo list
    found = changes.find_changes(root, table)
    snapshot.record_check(root, table, found)

    previous = before.get("files", {})
    now = table["files"]
    this_reply = [c for c in found
                  if previous.get(c.path, {}).get("status") != "changed"
                  or previous.get(c.path, {}).get("seen") != now.get(c.path, {}).get("seen")]
    if not this_reply and not committed:
        emit()  # nothing changed in this reply: stay silent
        return

    parts = []
    if this_reply:
        levels = [rules.score(rules.rules_for(c, found, root)) for c in this_reply]
        counts = ", ".join(f"{levels.count(level)} {level}" for level in ("HIGH", "MED", "LOW") if level in levels)
        word = "file" if len(this_reply) == 1 else "files"
        parts.append(f"{len(this_reply)} {word} changed ({counts})")
    if committed:
        saved = {c["path"]: c["risk"] for c in table.get("committed", [])}
        names = ", ".join(f"{c.path} {saved.get(c.path, '')}".strip() for c in committed)
        parts.append(f"committed: {names}")
    total = len(found)
    session = f" {total} changed this session." if total != len(this_reply) else ""
    emit(f"lineup: {'; '.join(parts)}.{session} Run lineup risky to see them.")


def on_prompt(root, event):
    """Before the user's next message reaches the agent: pass on the rollback note, if any."""
    note = rollback_note(root)
    emit(None, note, "UserPromptSubmit")


def run_hook(event_name, agent, stream=None):
    """Entry point for `lineup hook <event> --agent <agent>`. Never raises: a hook must not break the agent."""
    if loop_guard_on():
        emit()
        return 0
    event = read_event(stream)
    try:
        root = repo_root(event)
        if root is None:
            emit()
            return 0
        if event_name == "start":
            on_session_start(root, event, agent)
        elif event_name == "stop":
            on_stop(root, event)
        else:
            on_prompt(root, event)
    except Exception as error:  # report, but never stop the agent
        emit(f"lineup: hook skipped ({type(error).__name__}: {error})")
    return 0


# ---- lineup init: install (or remove) the hooks ----

def _command(event, agent):
    """The hook command. If lineup isn't installed or on PATH, the shell's error is swallowed and "{}"
    (nothing to say) is printed instead, so the agent carries on normally."""
    return f"lineup hook {event} --agent {agent} || echo {{}}"


def claude_hooks():
    return {
        "SessionStart": [{"matcher": "startup|resume|clear|compact|fork",
                          "hooks": [{"type": "command", "command": _command("start", "claude"), "timeout": 60}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _command("prompt", "claude"), "timeout": 30}]}],
        "Stop": [{"hooks": [{"type": "command", "command": _command("stop", "claude"), "timeout": 60}]}],
    }


def codex_hooks():
    return {
        "SessionStart": [{"hooks": [{"type": "command", "command": _command("start", "codex"), "timeout": 60}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _command("prompt", "codex"), "timeout": 30}]}],
        "Stop": [{"hooks": [{"type": "command", "command": _command("stop", "codex"), "timeout": 60}]}],
    }


# Claude: the personal, uncommitted settings file. Codex has no local-only file, so its repo file is used
# and kept out of git with .git/info/exclude.
TARGETS = {
    "claude": (".claude/settings.local.json", claude_hooks),
    "codex": (".codex/hooks.json", codex_hooks),
}


def _read_settings(root, rel):
    file = Path(root, rel)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"{rel} isn't valid JSON, so lineup won't change it ({error})") from error
    if not isinstance(data, dict):
        raise ValueError(f"{rel} isn't a JSON object, so lineup won't change it")
    return data


def has_lineup_hooks(root, rel):
    try:
        data = _read_settings(root, rel)
    except ValueError:
        return False
    return hookjson.without_lineup(data) != data


def installed(root):
    """True if lineup's hooks are in any of this repo's Claude or Codex settings files."""
    return any(has_lineup_hooks(root, rel) for rel in hookjson.SETTINGS_FILES)


def _has_command(groups, command):
    return any(h.get("command") == command for g in groups for h in g.get("hooks", []))


def plan_init(root, agents):
    """What `lineup init` would add: {file: (new_json, [(event, command)])}. Existing settings are kept."""
    plan = {}
    for agent in agents:
        rel, wanted = TARGETS[agent]
        data = _read_settings(root, rel)
        hooks = data.setdefault("hooks", {})
        added = []
        for event, groups in wanted().items():
            current = hooks.setdefault(event, [])
            for group in groups:
                command = group["hooks"][0]["command"]
                if not _has_command(current, command):
                    current.append(group)
                    added.append((event, command))
        if added:
            plan[rel] = (data, added)
    return plan


def plan_remove(root):
    """What `lineup init --remove` would take out: {file: (new_json or None to delete, [(event, command)])}."""
    plan = {}
    for rel in hookjson.SETTINGS_FILES:
        data = _read_settings(root, rel)
        kept = hookjson.without_lineup(data)
        if kept == data:
            continue
        removed = [(event, h["command"]) for event, groups in data.get("hooks", {}).items() if isinstance(groups, list)
                   for g in groups if isinstance(g, dict) for h in g.get("hooks", [])
                   if isinstance(h, dict) and hookjson.is_lineup_command(h.get("command"))]
        plan[rel] = (kept or None, removed)  # nothing left: the file was only lineup's, so delete it
    return plan


def write_plan(root, plan):
    """Write (or delete) the planned files: temp file, then swap in."""
    for rel, (data, _) in plan.items():
        file = Path(root, rel)
        if data is None:
            file.unlink(missing_ok=True)
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_name(file.name + ".lineup-tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, file)


def keep_out_of_git(root, rel):
    """Add `rel` to .git/info/exclude (the repo's private ignore list) unless git tracks or ignores it already.

    Returns True if it was added.
    """
    if git.is_tracked(root, rel) or git.is_ignored(root, rel):
        return False
    exclude = git.git_dir(root) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    line = f"/{rel}"
    if line in text.splitlines():
        return False
    with open(exclude, "a", encoding="utf-8") as f:
        f.write(("" if not text or text.endswith("\n") else "\n") + line + "\n")
    return True


def tracked_warnings(root):
    """Warnings for files holding lineup's hooks that git tracks (they'd be shared when committed)."""
    return [f"Warning: {rel} is tracked by git, so lineup's hooks in it would be shared when you commit it. "
            f"Run lineup init --remove, then lineup init, to move them to an uncommitted file."
            for rel in hookjson.SETTINGS_FILES if has_lineup_hooks(root, rel) and git.is_tracked(root, rel)]

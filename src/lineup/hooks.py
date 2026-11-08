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

SESSION_NOTE = ("This repo uses lineup, a tool that reviews agent changes. The user may run lineup diff, "
                "lineup risky or lineup rollback with !. Don't comment on or evaluate that output unless the user "
                "asks. Its scores are meant as a check that's independent of the agent. Rollback takes IDs or file "
                "names shown by lineup.")
MAX_HIGH_SHOWN = 5
PREFIX = "≈ lineup:"  # starts every message lineup shows from a hook


def on_session_start(root, event, agent):
    """Session start (startup, resume, clear, compact or fork): take or keep the photo.

    fork is treated like resume: the forked session continues the same work, so the same photo applies.
    """
    note = rollback_note(root)  # read before a fresh photo could replace the table
    table, kept = snapshot.start_session(root, agent=agent)
    if kept:
        word = "file" if kept == 1 else "files"
        message = (f"{PREFIX} keeping the earlier snapshot, {kept} uncommitted {word} from before this session. "
                   "Some may be your own edits.")
    else:
        label = "no commits yet" if table["start_commit"] == git.EMPTY_TREE else table["start_commit"][:7]
        message = f"{PREFIX} photo taken at {label} ({snapshot.summary(table)})"
    context = SESSION_NOTE + (f"\n{note}" if note else "")
    store.count_usage(root, sessions=1)
    if agent == "claude":
        # Claude Code doesn't display a session-start message, so it's shown once with the next
        # end-of-reply message instead (and never, if the session makes no edits).
        store.save_start_message(root, message)
        emit(None, context, "SessionStart")
    else:
        emit(message, context, "SessionStart")  # Codex shows a session-start systemMessage


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
    high = []  # (id, path, top rule note) for HIGH files changed in this reply
    if this_reply:
        levels = []
        for c in this_reply:
            flags = sorted(rules.rules_for(c, found, root), key=lambda f: -rules.LEVELS[f[0]])
            level = rules.score(flags)
            levels.append(level)
            if level == "HIGH":
                high.append((now.get(c.path, {}).get("id", "-"), c.path, flags[0][1]))
        store.count_usage(root, replies_with_changes=1, **{f"flagged_{level}": levels.count(level)
                                                            for level in ("HIGH", "MED", "LOW")})
        counts = ", ".join(f"{levels.count(level)} {level}" for level in ("HIGH", "MED", "LOW") if level in levels)
        word = "file" if len(this_reply) == 1 else "files"
        parts.append(f"{len(this_reply)} {word} changed ({counts})")
    if committed:
        saved = {c["path"]: c["risk"] for c in table.get("committed", [])}
        names = ", ".join(f"{c.path} {saved.get(c.path, '')}".strip() for c in committed)
        parts.append(f"committed: {names}")
    total = len(found)
    session = f" {total} changed this session." if total != len(this_reply) else ""
    if high:
        message = high_list(high, committed and parts[-1])
    else:
        message = f"{PREFIX} {'; '.join(parts)}.{session} Run lineup risky to see them."
    start = store.pop_start_message(root)  # saved at session start, shown once on top
    emit(f"{start}\n{message}" if start else message)


def high_list(high, committed_part=None):
    """The score message when something is HIGH: each HIGH file's ID, name and rule note, then how to undo."""
    shown = high[:MAX_HIGH_SHOWN]
    width = max(len(path) for _, path, _ in shown)
    lines = [f"\u26a0 {PREFIX} {len(high)} HIGH"]
    lines += [f"  {file_id} {path:<{width}}  {note}" for file_id, path, note in shown]
    if len(high) > MAX_HIGH_SHOWN:
        lines.append(f"  +{len(high) - MAX_HIGH_SHOWN} more")
    if committed_part:
        lines.append(f"  ({committed_part})")
    lines.append(f"Undo: lineup rollback {shown[0][0]}")
    return "\n".join(lines)


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
        emit(f"{PREFIX} hook skipped ({type(error).__name__}: {error})")
    return 0


# ---- lineup init: install (or remove) the hooks ----

def _command(event, agent, windows=None):
    """The hook command. If lineup isn't installed or on PATH, the shell's error is swallowed and "{}"
    (nothing to say) is printed instead, so the agent carries on normally.

    Codex on Windows runs hooks in PowerShell 5.1, which rejects `||`, so there the command runs in cmd.
    """
    command = f"lineup hook {event} --agent {agent} || echo {{}}"
    if agent == "codex" and (os.name == "nt" if windows is None else windows):
        return f'cmd /c "{command}"'
    return command


def claude_hooks():
    return {
        "SessionStart": [{"matcher": "startup|resume|clear|compact|fork",
                          "hooks": [{"type": "command", "command": _command("start", "claude"), "timeout": 60}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _command("prompt", "claude"), "timeout": 30}]}],
        "Stop": [{"hooks": [{"type": "command", "command": _command("stop", "claude"), "timeout": 60}]}],
    }


def codex_hooks(windows=None):
    def handler(event, timeout):
        return {"type": "command", "command": _command(event, "codex", windows), "timeout": timeout}
    return {
        "SessionStart": [{"hooks": [handler("start", 60)]}],
        "UserPromptSubmit": [{"hooks": [handler("prompt", 30)]}],
        "Stop": [{"hooks": [handler("stop", 60)]}],
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


def _lineup_commands(data):
    """[(event, command)] for every lineup hook in a settings object, in any form lineup ever wrote."""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    return [(event, h["command"]) for event, groups in hooks.items() if isinstance(groups, list)
            for g in groups if isinstance(g, dict) and isinstance(g.get("hooks"), list) for h in g["hooks"]
            if isinstance(h, dict) and hookjson.is_lineup_command(h.get("command"))]


def plan_init(root, agents):
    """What `lineup init` would change: {file: (new_json, [(event, added command)], [(event, replaced command)])}.

    lineup's older hook lines (e.g. 0.1.0's Codex lines on Windows) are replaced, never duplicated.
    Everything else in the file is kept.
    """
    plan = {}
    for agent in agents:
        rel, wanted = TARGETS[agent]
        original = _read_settings(root, rel)
        if original.get("hooks") is not None and not isinstance(original["hooks"], dict):
            raise ValueError(f"{rel} has a \"hooks\" entry that isn't a JSON object, so lineup won't change it")
        old = _lineup_commands(original)
        groups_wanted = wanted()
        new = [(event, g["hooks"][0]["command"]) for event, groups in groups_wanted.items() for g in groups]
        if sorted(old) == sorted(new):
            continue  # already installed, in the current form
        data = hookjson.without_lineup(original)
        hooks = data.setdefault("hooks", {})
        for event, groups in groups_wanted.items():
            hooks.setdefault(event, []).extend(groups)
        plan[rel] = (data, [p for p in new if p not in old], [p for p in old if p not in new])
    return plan


def plan_remove(root):
    """What `lineup init --remove` would take out: {file: (new_json or None to delete, [(event, command)])}."""
    plan = {}
    for rel in hookjson.SETTINGS_FILES:
        data = _read_settings(root, rel)
        kept = hookjson.without_lineup(data)
        if kept == data:
            continue
        plan[rel] = (kept or None, _lineup_commands(data))  # nothing left: the file was only lineup's, so delete it
    return plan


def write_plan(root, plan):
    """Write (or delete) the planned files: temp file, then swap in."""
    for rel, (data, *_) in plan.items():
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

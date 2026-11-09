"""The tester usage log: .git/lineup/log.jsonl, one JSON event per line, this repo only.

It holds file TYPES ("python", "test", "env"), rule types, counts, times and versions. Never file names,
paths, code, branch names or commit messages. Writing to it never raises and never slows a hook: one small
append per event, and the file is trimmed to the newest events when it passes MAX_BYTES.
"""

import json
import os
import platform
import posixpath
import re
import time
from datetime import datetime

from lineup import __version__, store

LOG = "log.jsonl"
MAX_BYTES = 5 * 1024 * 1024
KEEP_BYTES = 4 * 1024 * 1024  # after trimming: the newest ~4 MB, so trimming doesn't happen on every write

# A logged text value must look like a type or version, never like a name or a sentence.
SAFE_TEXT = re.compile(r"^[A-Za-z0-9_+-]+(\.[0-9]+)*$")

LANGUAGES = {
    ".py": "python", ".pyi": "python", ".ipynb": "notebook",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cc": "cpp", ".hpp": "cpp", ".sql": "sql", ".html": "web", ".css": "web", ".scss": "web", ".vue": "web",
    ".svelte": "web", ".sh": "script", ".ps1": "script", ".bat": "script", ".cmd": "script",
}
KINDS = {"private": "env", "test": "test", "config": "config", "docs": "docs", "lockfile": "lockfile",
         "dependencies": "dependencies", "schema": "schema"}

# Rule reasons (rules.py) to rule types. The reason text can hold names ("function added: login"), so only
# the part before ":" or "(" is used, and anything unknown becomes "other".
RULE_TYPES = {
    ".env or key file changed, contents not read": "private_file",
    "copy of a private file, contents not read": "private_copy",
    "possible secret": "possible_secret",
    "auth or payment file changed": "auth_file",
    "CI or agent settings changed": "ci_or_agent_settings",
    "lineup's own hooks": "lineup_hooks",
    "install script changed": "install_script",
    "migration or database schema changed": "schema",
    "assertion removed": "assertion_removed",
    "skip added": "skip_added",
    "test deleted": "test_deleted",
    "test file deleted": "test_file_deleted",
    "possible weakened test, check this": "weakened_test",
    "function deleted but still used": "function_deleted_still_used",
    "possible breakage": "possible_breakage",
    "config changed": "config",
    "new package": "new_package",
    "dependency file changed": "dependencies",
    "lockfile changed": "lockfile",
    "file deleted": "file_deleted",
    "lines removed": "many_lines_removed",
    "big file changed, can't be read": "big_file",
    "model file changed": "model_file",
    "function deleted": "function_deleted",
    "function moved": "function_moved",
    "function added": "function_added",
    "test added": "test_added",
    "new test file": "new_test_file",
    "new file": "new_file",
    "docs changed": "docs",
    "code edited": "code_edited",
    "changed": "changed",
}


def file_type(path):
    """A file's type, never its name: "env", "test", "config", "python", "other"..."""
    from lineup.changes import file_kind  # here, to keep imports one-way
    kind = file_kind(path)
    if kind in KINDS:
        return KINDS[kind]
    return LANGUAGES.get(posixpath.splitext(path.lower())[1], "other")


def rule_type(reason):
    text = re.split(r"[:(]", reason, maxsplit=1)[0].strip()
    text = re.sub(r"^\d+ ", "", text)  # "120 lines removed"
    return RULE_TYPES.get(text, "other")


def environment():
    return {"os": platform.system().lower() or "unknown", "python": platform.python_version(),
            "lineup": __version__}


def _safe(value):
    """Keep numbers, booleans, None and type-like text; drop anything else (it could be a name)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 40 and SAFE_TEXT.match(value) else "?"
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items() if isinstance(k, str) and SAFE_TEXT.match(k)}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return "?"


def record(root, event, **fields):
    """Append one event. Never raises: a logging problem must not break a hook or a command."""
    try:
        line = json.dumps({"t": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event,
                           **_safe(fields)}, separators=(",", ":"))
        file = store.folder(root) / LOG
        with open(file, "a", encoding="utf-8") as log:
            log.write(line + "\n")
        if file.stat().st_size > MAX_BYTES:
            _trim(file)
    except Exception:
        pass


def _trim(file):
    """Keep only the newest events, about KEEP_BYTES of them, cut at a line break."""
    data = file.read_bytes()
    tail = data[-KEEP_BYTES:]
    tail = tail[tail.find(b"\n") + 1:] if b"\n" in tail else b""
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_bytes(tail)
    os.replace(tmp, file)


def read(root):
    """Every readable event, oldest first. Bad lines are skipped."""
    file = store.folder(root) / LOG
    if not file.is_file():
        return []
    events = []
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("event"), str):
            events.append(event)
    return events


def has_data(root):
    """True if there's anything a tester might want to export: logged events or notes."""
    try:
        folder = store.git.git_dir(root) / "lineup"
        return any((folder / name).is_file() and (folder / name).stat().st_size for name in (LOG, store.NOTES))
    except Exception:
        return False


class Timer:
    """Seconds since it was made, rounded for the log."""

    def __init__(self):
        self.start = time.monotonic()

    def seconds(self):
        return round(time.monotonic() - self.start, 1)


def summarize(events):
    """Totals for lineup report and the export, from the log's events."""
    count = lambda name: sum(1 for e in events if e.get("event") == name)  # noqa: E731
    replies = [e for e in events if e.get("event") == "reply"]
    flags = {level: sum(_int(e.get("flags", {}).get(level)) for e in replies if isinstance(e.get("flags"), dict))
             for level in ("HIGH", "MED", "LOW")}
    commands = {}
    for e in events:
        if e.get("event") == "command" and isinstance(e.get("name"), str):
            commands[e["name"]] = commands.get(e["name"], 0) + 1
    rollbacks = {}
    for e in events:
        if e.get("event") == "rollback" and isinstance(e.get("outcome"), str):
            rollbacks[e["outcome"]] = rollbacks.get(e["outcome"], 0) + 1
    ai = [e for e in events if e.get("event") == "ai_summary"]
    seconds = [e["seconds"] for e in ai if isinstance(e.get("seconds"), (int, float))]
    tokens = [e["tokens"] for e in ai if isinstance(e.get("tokens"), int)]
    days = sorted({e["t"][:10] for e in events if isinstance(e.get("t"), str) and len(e["t"]) >= 10})
    agents = sorted({e["agent"] for e in events if isinstance(e.get("agent"), str) and e["agent"] != "?"})
    setup = [e for e in events if e.get("event") in ("session_start", "init")]
    last = setup[-1] if setup else {}
    changed = [_int(e.get("files_changed")) for e in replies]
    return {
        "sessions": count("session_start"), "days": days, "agents": agents,
        "replies": len(replies), "files_per_reply": changed, "flags": flags,
        "commands": commands, "rollbacks": rollbacks,
        "ai_runs": len(ai), "ai_seconds": seconds, "ai_tokens": tokens,
        "ai_failed": sum(1 for e in ai if e.get("result") == "failed"),
        "ai_timeouts": sum(1 for e in ai if e.get("result") == "timeout"),
        "hook_errors": count("hook_error"),
        "os": last.get("os"), "python": last.get("python"), "lineup": last.get("lineup"),
    }


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0

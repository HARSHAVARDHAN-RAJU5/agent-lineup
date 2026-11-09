"""Looks after lineup's drawer, .git/lineup/: the table, the copies and the diary.

The only module that reads or writes that folder. It saves and loads; it never decides what to save.
"""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from lineup import git

TABLE = "table.json"
DIARY = "audit.log"
COPIES = "copies"
SUMMARIES = "summaries.json"
BACKUPS = "backups"
AGENT_NOTE = "agent_note.json"
START_MESSAGE = "start_message.txt"
NOTES = "notes.jsonl"


def folder(root):
    """The drawer, created on first use."""
    drawer = git.git_dir(root) / "lineup"
    drawer.mkdir(exist_ok=True)
    return drawer


def has_snapshot(root):
    return (folder(root) / TABLE).is_file()


def load_table(root):
    """The table, or an empty dict if there's no photo yet."""
    file = folder(root) / TABLE
    if not file.is_file():
        return {}
    return json.loads(file.read_text(encoding="utf-8"))


def _save_json(root, name, data):
    """Write to a temp file first, then swap it in, so a crash never leaves half a file."""
    file = folder(root) / name
    tmp = file.with_name(name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, file)


def save_table(root, table):
    _save_json(root, TABLE, table)


def load_summaries(root):
    """Saved AI answers: {"overview": {...}, "files": {path: {"key", "line"}}}."""
    file = folder(root) / SUMMARIES
    if not file.is_file():
        return {"overview": {}, "files": {}}
    return json.loads(file.read_text(encoding="utf-8"))


def save_summaries(root, data):
    _save_json(root, SUMMARIES, data)


def add_rollback_note(root, paths):
    """Remember files the user rolled back, so the agent is told on its next turn (kept across photos)."""
    file = folder(root) / AGENT_NOTE
    current = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else []
    for path in paths:
        if path not in current:
            current.append(path)
    _save_json(root, AGENT_NOTE, current)


def save_start_message(root, message):
    """Keep the session-start message to show once with the next end-of-reply message (replaces an older one)."""
    (folder(root) / START_MESSAGE).write_text(message, encoding="utf-8")


def pop_start_message(root):
    """The saved session-start message, then forget it. None if there's none."""
    file = folder(root) / START_MESSAGE
    if not file.is_file():
        return None
    try:
        return file.read_text(encoding="utf-8").strip() or None
    finally:
        file.unlink(missing_ok=True)


def pop_rollback_note(root):
    """The rolled-back files waiting to be told to the agent, then forget them. [] if none."""
    file = folder(root) / AGENT_NOTE
    if not file.is_file():
        return []
    try:
        paths = json.loads(file.read_text(encoding="utf-8"))
    finally:
        file.unlink(missing_ok=True)
    return paths if isinstance(paths, list) else []


def clear_summaries(root):
    (folder(root) / SUMMARIES).unlink(missing_ok=True)


def _safe_parts(path):
    parts = PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
        raise ValueError(f"unsafe path: {path}")
    return parts


def _copy_path(root, path):
    """Where the copy of repo file `path` lives. Keeps its folders; refuses paths that escape."""
    return folder(root).joinpath(COPIES, *_safe_parts(path))


def _backup_path(root, run, path):
    if not re.fullmatch(r"[0-9A-Za-z_-]+", str(run)):
        raise ValueError(f"unsafe backup folder: {run}")
    return folder(root).joinpath(BACKUPS, str(run), *_safe_parts(path))


def backup_run_exists(root, run):
    return (folder(root) / BACKUPS / str(run)).exists()


def save_backup(root, run, path, data):
    """Keep the version a rollback is about to replace, in that rollback's own folder. Never overwrites."""
    target = _backup_path(root, run, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def read_backup(root, run, path):
    target = _backup_path(root, run, path)
    return target.read_bytes() if target.is_file() else None


def backup_runs(root):
    """Backup folder names, oldest first."""
    backups = folder(root) / BACKUPS
    return sorted(p.name for p in backups.iterdir() if p.is_dir()) if backups.is_dir() else []


def delete_backups(root, path):
    """Remove every backup of `path` (used when the file is committed)."""
    backups = folder(root) / BACKUPS
    if not backups.is_dir():
        return
    for id_folder in backups.iterdir():
        target = id_folder.joinpath(*_safe_parts(path))
        if target.is_file():
            target.unlink()
            _remove_empty_folders(target.parent, backups)


def _remove_empty_folders(start, stop):
    """Remove `start` and its parents while they're empty, never `stop` itself."""
    parent = start
    while parent != stop and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def save_copy(root, path, data):
    """Save an exact byte-for-byte copy of a file."""
    target = _copy_path(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def read_copy(root, path):
    """The saved copy as bytes, or None if there isn't one."""
    target = _copy_path(root, path)
    return target.read_bytes() if target.is_file() else None


def delete_copy(root, path):
    """Remove a copy, and any folders left empty by it."""
    target = _copy_path(root, path)
    if target.is_file():
        target.unlink()
    _remove_empty_folders(target.parent, folder(root) / COPIES)


def log(root, message):
    """Add one line to the diary. Never overwrites; newlines are squashed so code can't slip in."""
    line = " ".join(str(message).split())
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(folder(root) / DIARY, "a", encoding="utf-8") as diary:
        diary.write(f"{stamp} {line}\n")


def read_log(root):
    file = folder(root) / DIARY
    return file.read_text(encoding="utf-8").splitlines() if file.is_file() else []


def save_note(root, text):
    """Append a tester's note (already cleaned of secrets) with the time it was written."""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with open(folder(root) / NOTES, "a", encoding="utf-8") as notes:
        notes.write(json.dumps({"t": stamp, "text": text}, ensure_ascii=False) + "\n")


def load_notes(root):
    """[(time, text)] oldest first. Bad lines are skipped."""
    file = folder(root) / NOTES
    if not file.is_file():
        return []
    notes = []
    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            note = json.loads(line)
        except ValueError:
            continue
        if isinstance(note, dict) and isinstance(note.get("text"), str):
            notes.append((str(note.get("t", "")), note["text"]))
    return notes


def delete_all(root):
    """Delete the whole drawer (photo, copies, backups, logs, notes). Refuses if it's a link or junction."""
    from lineup.paths import is_link
    drawer = git.git_dir(root) / "lineup"
    if not drawer.exists():
        return False
    if is_link(drawer):
        raise OSError(f"{drawer} is a link, so lineup won't delete through it")
    shutil.rmtree(drawer)
    return True


def clear_copies(root):
    """Remove every saved copy (used when a fresh photo replaces the old one)."""
    shutil.rmtree(folder(root) / COPIES, ignore_errors=True)

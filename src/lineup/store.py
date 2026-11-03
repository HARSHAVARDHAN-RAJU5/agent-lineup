"""Looks after lineup's drawer, .git/lineup/: the table, the copies and the diary.

The only module that reads or writes that folder. It saves and loads; it never decides what to save.
"""

import json
import os
from datetime import datetime
from pathlib import Path, PurePosixPath

from lineup import git

TABLE = "table.json"
DIARY = "audit.log"
COPIES = "copies"


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


def save_table(root, table):
    """Write to a temp file first, then swap it in, so a crash never leaves half a table."""
    file = folder(root) / TABLE
    tmp = file.with_name(TABLE + ".tmp")
    tmp.write_text(json.dumps(table, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, file)


def _copy_path(root, path):
    """Where the copy of repo file `path` lives. Keeps its folders; refuses paths that escape."""
    parts = PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
        raise ValueError(f"unsafe path: {path}")
    return folder(root).joinpath(COPIES, *parts)


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
    copies = folder(root) / COPIES
    parent = target.parent
    while parent != copies and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def log(root, message):
    """Add one line to the diary. Never overwrites; newlines are squashed so code can't slip in."""
    line = " ".join(str(message).split())
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(folder(root) / DIARY, "a", encoding="utf-8") as diary:
        diary.write(f"{stamp} {line}\n")


def read_log(root):
    file = folder(root) / DIARY
    return file.read_text(encoding="utf-8").splitlines() if file.is_file() else []

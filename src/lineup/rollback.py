"""Undo files by ID, from the session photo. No AI.

Never reads, writes or deletes anything behind a link or outside the repo (see paths.py).
"""

import os
import posixpath
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lineup import changes, git, paths, rules, store
from lineup.guard import is_private

REFUSE_PRIVATE = "private file (like .env) changed, can't undo this, please check it yourself"
REFUSE_COPY = "copy of a private file, can't undo this, please check it yourself"
REFUSE_BIG = "big file, no copy was kept, so it can't be rolled back"
REFUSE_LINK = "symlink, not followed, so it can't be rolled back"
REFUSE_COMMITTED = "committed, lineup can't undo it; use git to undo"
REFUSE_OUTSIDE = "outside the repo, lineup never touches it"
REFUSE_FOLDER = "a folder now has this name, so nothing was changed. Move the folder away first to get the file back"
WARN_CHANGED = ("This file changed since lineup last checked it, and it may include your edits. "
                "Your current version is saved in backups.")


@dataclass
class Undo:
    id: int
    path: str
    action: str  # "restore", "bring back", "delete", "refuse" or "nothing"
    before: bytes = None  # the version to write back (restore / bring back)
    reason: str = ""  # why it's refused, or why there's nothing to do
    executable: bool = False  # bring back with the executable permission
    warnings: list = field(default_factory=list)


class UnknownId(Exception):
    pass


def valid_ids(table):
    """IDs that can be named in `lineup rollback`: files changed or committed this session."""
    return sorted(row["id"] for row in table["files"].values() if "id" in row)


def resolve(table, token):
    """Turn what the user typed (an ID like 3, or a name like auth.py) into an ID.

    Returns (id, None), or (None, problem) when nothing or more than one file matches.
    """
    named = {path: row["id"] for path, row in table["files"].items() if "id" in row}
    text = token.strip().replace("\\", "/")
    if text.isdigit():
        return int(text), None
    text = text[2:] if text.startswith("./") else text
    if text in named:
        return named[text], None
    matches = sorted((named[p], p) for p in named if posixpath.basename(p) == text or p.endswith("/" + text))
    if len(matches) == 1:
        return matches[0][0], None
    if matches:
        listed = ", ".join(f"{i} {p}" for i, p in matches)
        return None, f"{token} matches more than one file: {listed}. Use the ID."
    return None, f"No changed file named {token}"


def _row_for(table, file_id):
    for path, row in table["files"].items():
        if row.get("id") == file_id:
            return path, row
    ids = ", ".join(str(i) for i in valid_ids(table)) or "none yet (run lineup diff first)"
    raise UnknownId(f"no file with ID {file_id}. Valid IDs: {ids}")


def _blocked(root, path):
    """Why writing `path` is impossible or unsafe, or None."""
    reason = paths.unsafe(root, path)
    if reason == paths.OUTSIDE:
        return REFUSE_OUTSIDE
    if reason:
        return f"{reason}, so it can't be rolled back"
    file = Path(root, path)
    if file.is_dir():
        return REFUSE_FOLDER
    parent = file.parent
    while parent != Path(root) and not parent.exists():
        parent = parent.parent
    if parent != Path(root) and not parent.is_dir():
        return f"a file now has the name of its folder ({parent.relative_to(root).as_posix()}), so nothing was changed"
    return None


def plan_undo(root, table, file_id, last_check=None):
    """What undoing file `file_id` would do. Reads only; changes nothing on disk.

    `last_check` is the table as it was before this command, to spot files changed since the last check.
    """
    path, row = _row_for(table, file_id)
    file = Path(root, path)
    at_start = row.get("at_start")

    def refuse(reason):
        return Undo(file_id, path, "refuse", reason=reason)

    blocked = _blocked(root, path)  # first: never look behind a link or outside the repo
    if blocked:
        return refuse(blocked)
    if row.get("status") == "committed":
        return refuse(REFUSE_COMMITTED)
    if at_start == "symlink" or changes.is_link(file):
        return refuse(REFUSE_LINK)
    if row.get("copy_of_private"):
        return refuse(REFUSE_COPY)
    if at_start == "private" or is_private(path):
        return refuse(REFUSE_PRIVATE)
    if at_start == "big":
        return refuse(REFUSE_BIG)
    if changes.is_copy_of_private(file, changes.private_fingerprints(root, table["files"])):
        return refuse(REFUSE_COPY)

    # The "before": the saved copy, nothing (didn't exist), or git (exactly as git would write it).
    executable = False
    if at_start == "uncommitted":
        before = store.read_copy(root, path)
        if before is None:
            return refuse("the saved copy is missing, so it can't be rolled back")
        executable = bool(row.get("mode", 0) & 0o111)
    elif at_start == "missing":
        before = None
    else:
        rev = row.get("base", table["start_commit"])
        before = git.checkout_bytes(root, rev, path)
        executable = git.file_mode(root, rev, path) == "100755"

    exists_now = os.path.lexists(file)
    if before is None:
        undo = Undo(file_id, path, "delete") if exists_now else \
            Undo(file_id, path, "nothing", reason="already gone, as at the start")
    elif not exists_now:
        undo = Undo(file_id, path, "bring back", before=before, executable=executable)
    elif file.read_bytes() == before:
        undo = Undo(file_id, path, "nothing", reason="already the same as at the start")
    else:
        undo = Undo(file_id, path, "restore", before=before)

    seen = ((last_check or {}).get("files", {}).get(path) or {}).get("seen")
    if undo.action in DONE and seen and seen != changes.seen_fingerprint(root, path):
        undo.warnings.append(WARN_CHANGED)
    return undo


# ---- back up, then write back or delete ----

DONE = {"restore": "restored", "bring back": "brought back", "delete": "deleted"}


def new_backup_run(root):
    """A fresh backup folder name for one rollback run, like 2026-11-05_143012 (never reused)."""
    name = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run, n = name, 2
    while store.backup_run_exists(root, run):
        run, n = f"{name}-{n}", n + 1
    return run


def apply_undo(root, undo, run):
    """Carry out a plan from plan_undo(). Backs up the current file into backup folder `run` first.

    Returns a short result word. Never raises for a file problem: it returns "failed: ..." instead.
    """
    if undo.action not in DONE:
        word = "refused" if undo.action == "refuse" else "nothing to do"
        store.log(root, f"rollback {undo.id}: {undo.path} {word}")
        return word

    blocked = _blocked(root, undo.path)  # check again right before touching the disk
    if blocked:
        undo.action, undo.reason = "refuse", blocked
        store.log(root, f"rollback {undo.id}: {undo.path} refused")
        return "refused"

    file = Path(root, undo.path)
    try:
        if file.is_file() and not changes.is_link(file):
            store.save_backup(root, run, undo.path, file.read_bytes())  # so the undo can be undone
        if undo.action == "delete":
            file.unlink()
            _remove_empty_folders(file.parent, Path(root))
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(undo.before)
            if undo.action == "bring back" and undo.executable:
                os.chmod(file, stat.S_IMODE(os.stat(file).st_mode) | 0o111)
    except OSError as error:
        word = f"failed: {error.strerror or error}"
        store.log(root, f"rollback {undo.id}: {undo.path} failed")
        return word

    word = DONE[undo.action]
    store.log(root, f"rollback {undo.id}: {undo.path} {word}")
    return word


# ---- step 4: advice about other files (no AI, never rolls anything back) ----

def _named(change, table):
    file_id = table["files"].get(change.path, {}).get("id")
    return f"{change.path} (ID {file_id})" if file_id else change.path


def other_files_notes(root, table, rolled_back, named_ids):
    """Lines after a rollback: files probably linked by name, and every other file still changed.

    `rolled_back` are the paths actually rolled back; `named_ids` are all IDs the user typed.
    Returns [] when there's nothing to say.
    """
    if not rolled_back:
        return []
    named_paths = {path for path, row in table["files"].items() if row.get("id") in named_ids}
    still = [c for c in changes.find_changes(root, table) if c.path not in named_paths]

    linked = []
    for path in rolled_back:
        if changes.file_kind(path) == "test":
            linked += [c for c in still if c.file_kind != "test" and rules._stem(c.path) == rules.code_name(path)]
        else:
            linked += [c for c in still if c.file_kind == "test" and rules.code_name(c.path) == rules._stem(path)]
    linked = list({c.path: c for c in linked}.values())
    others = [c for c in still if c.path not in {c.path for c in linked}]

    lines = []
    if linked:
        names = ", ".join(_named(c, table) for c in linked)
        ids = " ".join(str(table["files"][c.path]["id"]) for c in linked if "id" in table["files"].get(c.path, {}))
        verb, pronoun = ("changed", "it") if len(linked) == 1 else ("changed", "them")
        tail = f" Roll {pronoun} back too? lineup rollback {ids}" if ids else ""
        lines.append(f"Probably linked: {names} also {verb}.{tail}")
    if others:
        names = ", ".join(_named(c, table) for c in others)
        subject = "it depends" if len(others) == 1 else "they depend"
        target = "this file" if len(rolled_back) == 1 else "these files"
        lines.append(f"Also changed: {names}. Check whether {subject} on {target}.")
    return lines


def _remove_empty_folders(start, root):
    """Remove folders left empty by a delete, never the repo folder itself."""
    parent = start
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent

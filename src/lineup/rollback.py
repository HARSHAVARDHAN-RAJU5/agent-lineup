"""Undo files by ID, from the session photo. No AI. Step 1: decide what undoing means (reads only)."""

import os
from dataclasses import dataclass
from pathlib import Path

from lineup import changes, git, store
from lineup.guard import is_private

REFUSE_PRIVATE = "private file (like .env) changed, can't undo this, please check it yourself"
REFUSE_COPY = "copy of a private file, can't undo this, please check it yourself"
REFUSE_BIG = "big file, no copy was kept, so it can't be rolled back"
REFUSE_LINK = "symlink, not followed, so it can't be rolled back"
REFUSE_COMMITTED = "committed, lineup can't undo it; use git to undo"


@dataclass
class Undo:
    id: int
    path: str
    action: str  # "restore", "bring back", "delete", "refuse" or "nothing"
    before: bytes = None  # the version to write back (restore / bring back)
    reason: str = ""  # why it's refused, or why there's nothing to do


class UnknownId(Exception):
    pass


def valid_ids(table):
    """IDs that can be named in `lineup rollback`: files changed or committed this session."""
    return sorted(row["id"] for row in table["files"].values() if "id" in row)


def _row_for(table, file_id):
    for path, row in table["files"].items():
        if row.get("id") == file_id:
            return path, row
    ids = ", ".join(str(i) for i in valid_ids(table)) or "none yet (run lineup diff first)"
    raise UnknownId(f"no file with ID {file_id}. Valid IDs: {ids}")


def plan_undo(root, table, file_id):
    """What undoing file `file_id` would do. Reads only; changes nothing on disk."""
    path, row = _row_for(table, file_id)
    file = Path(root, path)
    at_start = row.get("at_start")

    def refuse(reason):
        return Undo(file_id, path, "refuse", reason=reason)

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

    # The "before": the saved copy, nothing (didn't exist), or git at the start (or at a later commit).
    if at_start == "uncommitted":
        before = store.read_copy(root, path)
        if before is None:
            return refuse("the saved copy is missing, so it can't be rolled back")
    elif at_start == "missing":
        before = None
    else:
        before = git.show_file(root, row.get("base", table["start_commit"]), path)

    exists_now = os.path.lexists(file)
    if before is None:
        if exists_now:
            return Undo(file_id, path, "delete")
        return Undo(file_id, path, "nothing", reason="already gone, as at the start")
    if not exists_now:
        return Undo(file_id, path, "bring back", before=before)
    if file.read_bytes() == before:
        return Undo(file_id, path, "nothing", reason="already the same as at the start")
    return Undo(file_id, path, "restore", before=before)


# ---- step 2: back up, then write back or delete ----

DONE = {"restore": "restored", "bring back": "brought back", "delete": "deleted"}


def apply_undo(root, undo):
    """Carry out a plan from plan_undo(). Backs up the current file first. Returns a short result word."""
    if undo.action not in DONE:
        word = "refused" if undo.action == "refuse" else "nothing to do"
        store.log(root, f"rollback {undo.id}: {undo.path} {word}")
        return word

    file = Path(root, undo.path)
    if file.is_file() and not changes.is_link(file):
        store.save_backup(root, undo.id, undo.path, file.read_bytes())  # so the undo can be undone

    if undo.action == "delete":
        file.unlink()
        _remove_empty_folders(file.parent, Path(root))
    else:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(undo.before)

    word = DONE[undo.action]
    store.log(root, f"rollback {undo.id}: {undo.path} {word}")
    return word


def _remove_empty_folders(start, root):
    """Remove folders left empty by a delete, never the repo folder itself."""
    parent = start
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent

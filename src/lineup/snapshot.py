"""Takes the "before" photo at session start: decides what to keep, and asks store.py to save it."""

from datetime import datetime
from pathlib import Path

from lineup import changes, git, store
from lineup.guard import is_private


def _row(root, path):
    """What to keep about one file that isn't committed."""
    file = Path(root, path)
    if not file.is_file():
        return {"at_start": "missing"}
    if is_private(path):
        return {"at_start": "private", "hash": changes.fingerprint(file)}
    stat = file.stat()
    if stat.st_size > changes.BIG_FILE_BYTES:
        return {"at_start": "big", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    store.save_copy(root, path, file.read_bytes())
    return {"at_start": "uncommitted", "copy": True}


def take(root, keep=False):
    """Take the photo and return the table. With keep=True, an existing photo is left as it is."""
    if keep and store.has_snapshot(root):
        table = store.load_table(root)
        store.log(root, f"photo kept (taken {table['taken_at']} at {table['start_commit'][:7]})")
        return table

    store.clear_copies(root)
    head = git.head_commit(root)
    base = head or git.EMPTY_TREE

    files = {}
    not_committed = [path for _, path in git.changed_files(root, base)] + git.untracked_files(root)
    for path in sorted(set(not_committed)):
        files[path] = _row(root, path)
    for path in git.ignored_files(root):
        if not path.endswith("/") and is_private(path):
            files[path] = _row(root, path)

    table = {
        "start_commit": base,
        "last_seen_commit": base,
        "taken_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "next_id": 1,
        "files": files,
    }
    store.save_table(root, table)
    store.log(root, f"snapshot at {base[:7]}: {summary(table)}")
    return table


def counts(table):
    rows = table["files"].values()
    return {kind: sum(1 for r in rows if r["at_start"] == kind)
            for kind in ("uncommitted", "private", "big", "missing")}


def summary(table):
    c = counts(table)
    parts = [f"{c['uncommitted']} {'copy' if c['uncommitted'] == 1 else 'copies'}",
             f"{c['private']} fingerprint{'' if c['private'] == 1 else 's'}"]
    if c["big"]:
        parts.append(f"{c['big']} big file{'' if c['big'] == 1 else 's'} can't be undone")
    return ", ".join(parts)


def check_commits(root, table):
    """Files in commits made since the last check: flag them first, then take them off the undo list.

    Returns [(path, file kind)] of the flagged files.
    """
    head = git.head_commit(root)
    last = table["last_seen_commit"]
    if not head or head == last:
        return []

    flagged = [(path, changes.file_kind(path)) for path in git.files_between(root, last, head)]
    for path, kind in flagged:  # 1. flag first
        store.log(root, f"commit {head[:7]}: {path} ({kind}) flagged")
    for path, _ in flagged:  # 2. then off the undo list
        row = table["files"].get(path, {})
        if row.get("copy"):
            store.delete_copy(root, path)
        new_row = {"status": "committed", "base": head}
        if "id" in row:
            new_row["id"] = row["id"]
        table["files"][path] = new_row
        store.log(root, f"commit {head[:7]}: {path} taken off the undo list")

    table["last_seen_commit"] = head
    store.save_table(root, table)
    return flagged


def record_check(root, table, found):
    """Give each changed file a fixed ID and mark what changed in this check."""
    changed = set()
    for change in found:
        row = table["files"].setdefault(change.path, {})
        if "id" not in row:
            row["id"] = table["next_id"]
            table["next_id"] += 1
        row["status"] = "changed"
        changed.add(change.path)
    for path, row in table["files"].items():
        if row.get("status") == "changed" and path not in changed:
            row["status"] = "unchanged"  # e.g. Claude put it back the way it was

    table["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    store.save_table(root, table)
    names = ", ".join(c.path for c in found)
    word = "file" if len(found) == 1 else "files"
    store.log(root, f"checked: {len(found)} {word} changed ({names})" if found else "checked: no changes")


def start_session(root, fresh=False):
    """A new session or /clear. Returns (table, number of earlier uncommitted files kept).

    If the old photo still has uncommitted changes, keep it so they stay flagged and can be
    rolled back. Otherwise (or with fresh=True) take a new photo.
    """
    if fresh or not store.has_snapshot(root):
        return take(root), 0
    table = store.load_table(root)
    check_commits(root, table)
    found = changes.find_changes(root, table)
    if not found:
        return take(root), 0
    record_check(root, table, found)
    word = "file" if len(found) == 1 else "files"
    store.log(root, f"new session: kept earlier snapshot, {len(found)} uncommitted {word}")
    return table, len(found)

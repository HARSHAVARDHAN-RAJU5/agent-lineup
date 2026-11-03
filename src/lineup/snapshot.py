"""Takes the "before" photo at session start: decides what to keep, and asks store.py to save it."""

import hashlib
from datetime import datetime
from pathlib import Path

from lineup import changes, git, store
from lineup.guard import is_private


def fingerprint(file):
    """SHA-256 of a file: changes if the file changes, but can't be turned back into it."""
    digest = hashlib.sha256()
    with open(file, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row(root, path):
    """What to keep about one file that isn't committed."""
    file = Path(root, path)
    if not file.is_file():
        return {"at_start": "missing"}
    if is_private(path):
        return {"at_start": "private", "hash": fingerprint(file)}
    if file.stat().st_size > changes.BIG_FILE_BYTES:
        return {"at_start": "big"}
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

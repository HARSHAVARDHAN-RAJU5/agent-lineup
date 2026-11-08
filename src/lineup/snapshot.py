"""Takes the "before" photo at session start: decides what to keep, and asks store.py to save it."""

from datetime import datetime
from pathlib import Path

from lineup import changes, git, paths, rules, store
from lineup.guard import is_private, private_pathspecs


def _row(root, path, secret_hashes=frozenset()):
    """What to keep about one file that isn't committed. A copy of a private file only gets a fingerprint."""
    file = Path(root, path)
    reason = paths.unsafe(root, path)
    if reason == paths.OUTSIDE:
        return None  # never copy anything outside the repo
    if reason and paths.link_in_folders(root, path):  # behind a linked folder: nothing is read
        return {"at_start": "symlink"}
    if changes.is_link(file):  # never follow it: keep only where it points
        return {"at_start": "symlink", "link": changes.link_hash(file)}
    if not file.is_file():
        return {"at_start": "missing"}
    if is_private(path):
        return {"at_start": "private", "hash": changes.fingerprint(file)}
    if changes.is_copy_of_private(file, secret_hashes):  # e.g. .env renamed to settings.txt
        return {"at_start": "private", "hash": changes.fingerprint(file), "copy_of_private": True}
    stat = file.stat()
    if stat.st_size > changes.BIG_FILE_BYTES:
        return {"at_start": "big", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    store.save_copy(root, path, file.read_bytes())
    return {"at_start": "uncommitted", "copy": True, "mode": stat.st_mode & 0o777}


def take(root, keep=False):
    """Take the photo and return the table. With keep=True, an existing photo is left as it is."""
    if keep and store.has_snapshot(root):
        table = store.load_table(root)
        store.log(root, f"photo kept (taken {table['taken_at']} at {table['start_commit'][:7]})")
        return table

    old_rows = store.load_table(root).get("files", {})  # carries private fingerprints forward
    store.clear_copies(root)
    store.clear_summaries(root)  # new session: old AI answers no longer apply
    head = git.head_commit(root)
    base = head or git.EMPTY_TREE

    files = {}
    not_committed = changes.kept(root, sorted(set([path for _, path in git.changed_files(root, base)]
                                                  + git.untracked_files(root))))  # no caches or ignored files
    secret_hashes = changes.private_fingerprints(root, old_rows, not_committed)
    for path in not_committed:
        row = _row(root, path, secret_hashes)
        if row:
            files[path] = row
    for path in git.ignored_files(root, private_pathspecs()):  # names only: private ones get a fingerprint
        if is_private(path):
            row = _row(root, path)
            if row:
                files[path] = row

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

    Returns the flagged files as changes, each compared with its version before the commits.
    """
    head = git.head_commit(root)
    last = table["last_seen_commit"]
    if not head or head == last:
        return []

    flagged = changes.committed_changes(root, last, head)
    for change in flagged:  # 1. flag first: score it and keep the score (names and reasons only, no code)
        flags = sorted(rules.rules_for(change, flagged, root), key=lambda f: -rules.LEVELS[f[0]])
        table.setdefault("committed", []).append({
            "commit": head, "path": change.path, "risk": rules.score(flags),
            "reasons": [reason for _, reason in flags],
        })
        store.log(root, f"commit {head[:7]}: {change.path} ({change.file_kind}) flagged {rules.score(flags)}")
    for path in changes.kept(root, git.files_between(root, last, head)):  # 2. then off the undo list
        row = table["files"].get(path, {})
        if row.get("copy"):
            store.delete_copy(root, path)
        store.delete_backups(root, path)  # committed: its rollback backups aren't needed any more
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
        seen = changes.seen_fingerprint(root, change.path)  # lets rollback spot edits made after this check
        if seen:
            row["seen"] = seen
        else:
            row.pop("seen", None)
        changed.add(change.path)
    for path, row in table["files"].items():
        if row.get("status") == "changed" and path not in changed:
            row["status"] = "unchanged"  # e.g. Claude put it back the way it was

    table["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    store.save_table(root, table)
    names = ", ".join(c.path for c in found)
    word = "file" if len(found) == 1 else "files"
    store.log(root, f"checked: {len(found)} {word} changed ({names})" if found else "checked: no changes")


def _set_agent(root, table, agent):
    if agent and table.get("agent") != agent:
        table["agent"] = agent  # which agent's session this is: it writes the summaries
        store.save_table(root, table)
    return table


def start_session(root, fresh=False, agent=None):
    """A new session or /clear. Returns (table, number of earlier uncommitted files kept).

    If the old photo still has uncommitted changes, keep it so they stay flagged and can be
    rolled back. Otherwise (or with fresh=True) take a new photo. `agent` is saved in the photo.
    """
    if fresh or not store.has_snapshot(root):
        return _set_agent(root, take(root), agent), 0
    table = store.load_table(root)
    check_commits(root, table)
    found = changes.find_changes(root, table)
    if not found:
        return _set_agent(root, take(root), agent), 0
    _set_agent(root, table, agent)
    record_check(root, table, found)
    word = "file" if len(found) == 1 else "files"
    store.log(root, f"new session: kept earlier snapshot, {len(found)} uncommitted {word}")
    return table, len(found)

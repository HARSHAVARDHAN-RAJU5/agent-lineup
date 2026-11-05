"""Finds what changed in the repo and sorts it into Additions, Removals, Edits and Schema."""

import difflib
import hashlib
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from lineup import functions, git, hookjson, paths, store, table
from lineup.guard import is_private, private_pathspecs

BIG_FILE_BYTES = 10 * 1024 * 1024

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
    "cargo.lock", "go.sum", "composer.lock", "gemfile.lock", "uv.lock", "bun.lockb",
}


@dataclass
class Change:
    path: str
    kind: str  # "added", "deleted" or "modified"
    file_kind: str = "code"
    private: bool = False
    big: bool = False
    binary: bool = False
    symlink: bool = False  # never followed: only where it points is compared
    copy_of_private: bool = False  # same bytes as a private file (e.g. .env renamed to settings.txt)
    lineup_hooks_only: bool = False  # an agent settings file where only lineup's own hooks changed
    lines_added: int = 0
    lines_removed: int = 0
    diff: str = ""  # never filled for private, big, binary or symlink files
    funcs_added: list = field(default_factory=list)
    funcs_removed: list = field(default_factory=list)
    deps_added: list = field(default_factory=list)


# ---- what kind of file is this (by path only) ----

_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
_TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|^conftest\.py$)")
_SCHEMA = re.compile(r"(^|/)(migrations?|alembic)/|\.sql$|(^|/)schema\.(prisma|rb|sql)$|(^|/)db/schema")
_DOCS = re.compile(r"\.(md|rst|adoc)$|^docs?/|(^|/)(readme|changelog|license)(\.\w+)?$", re.I)
_CONFIG = re.compile(r"\.(ya?ml|toml|ini|cfg|conf)$|(^|/)dockerfile|(^|/)docker-compose|(^|/)\.github/|(^|/)\.claude/", re.I)
_AUTH_WORDS = {
    "auth", "authn", "authz", "authenticate", "authentication", "authorize", "authorization", "oauth",
    "login", "logins", "session", "sessions", "password", "passwords",
    "payment", "payments", "billing", "checkout", "checkouts",
}
# Words in a path: split at / _ - . digits and capitals. "src/AuthService.ts" -> src, Auth, Service, ts
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def path_words(path):
    return {word.lower() for word in _WORD.findall(path)}


def is_auth(path):
    """Whole words only: auth_service.py and AuthService.ts match, author.py and authority.py don't."""
    return bool(path_words(path) & _AUTH_WORDS)


def file_kind(path):
    """One word describing the file: private, test, schema, lockfile, dependencies, config, docs, auth or code."""
    name = posixpath.basename(path).lower()
    parts = set(path.lower().split("/")[:-1])
    if is_private(path):
        return "private"
    if parts & _TEST_DIRS or _TEST_NAME.search(name):
        return "test"
    if _SCHEMA.search(path.lower()):
        return "schema"
    if name in LOCKFILES:
        return "lockfile"
    if functions.is_dependency_file(path):
        return "dependencies"
    if _CONFIG.search(path):
        return "config"
    if _DOCS.search(path):
        return "docs"
    if is_auth(path):
        return "auth"
    return "code"


# ---- reading both versions ----

def _is_binary(data):
    return b"\0" in data[:8192]


def _decode(data):
    return data.decode("utf-8", "replace") if data is not None else ""


def fingerprint(file):
    """SHA-256 of a file: changes if the file changes, but can't be turned back into it."""
    digest = hashlib.sha256()
    with open(file, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(file):
    """True for a symlink or a Windows junction (a shortcut). Checked without opening it."""
    return paths.is_link(file)


def read_link(file):
    """Where a link points, as bytes. Reads the link itself, never the file it points to."""
    return os.fsencode(os.readlink(file))


def link_hash(file):
    """Fingerprint of where a link points (never of what it points to)."""
    return hashlib.sha256(read_link(file)).hexdigest()


def _link_change(root, base, row, path, file):
    """A path that is a symlink now: compare only where it points. Never opens the target."""
    target = read_link(file)
    at_start = (row or {}).get("at_start")
    if at_start == "symlink":
        if hashlib.sha256(target).hexdigest() == row.get("link"):
            return None
        kind = "modified"
    elif at_start == "missing":
        kind = "added"
    elif at_start:
        kind = "modified"
    else:
        old = git.show_file(root, (row or {}).get("base", base), path)  # git stores a link as its target
        if old == target:
            return None
        kind = "added" if old is None else "modified"
    private = is_private(path)
    return Change(path=path, kind=kind, file_kind="private" if private else file_kind(path),
                  private=private, symlink=True)


def _build(root, path, kind, old_size, load_old, new_size=None, load_new=None):
    """Compare the before and after of one file. Returns None if nothing really changed.

    The "after" is the file on disk, unless `new_size` and `load_new` give another version.
    """
    change = Change(path=path, kind=kind, file_kind=file_kind(path))
    if change.file_kind == "private":
        change.private = True
        return change  # never read a private file
    file = Path(root, path)
    if new_size is None:
        if kind != "deleted" and is_link(file):
            change.symlink = True
            return change  # never follow a link
        new_size = file.stat().st_size if kind != "deleted" else 0
        load_new = file.read_bytes
    if max(old_size, new_size) > BIG_FILE_BYTES:
        change.big = True
        return change

    old = (load_old() or b"") if kind != "added" else b""
    new = (load_new() or b"") if kind != "deleted" else b""
    if kind == "modified" and old == new:
        return None
    if _is_binary(old) or _is_binary(new):
        change.binary = True
        return change

    old_text, new_text = _decode(old), _decode(new)
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, f"a/{path}", f"b/{path}", lineterm=""))
    if kind == "modified" and not diff:
        return None  # e.g. only line endings differ
    body = diff[2:]  # skip the ---/+++ header
    change.lines_added = sum(1 for line in body if line.startswith("+"))
    change.lines_removed = sum(1 for line in body if line.startswith("-"))
    change.diff = "\n".join(diff)
    if path in hookjson.SETTINGS_FILES:
        change.lineup_hooks_only = hookjson.only_lineup_changed(old_text, new_text)
    change.funcs_added, change.funcs_removed = functions.function_changes(path, old_text, new_text)
    change.deps_added = functions.new_dependencies(path, old_text, new_text)
    return change


SECRET_COPY_MAX_BYTES = 1024 * 1024  # private files are small; don't fingerprint big files for this


def private_fingerprints(root, rows=None, changed=()):
    """Fingerprints of every private file we know: from the photo, and private files on disk now."""
    hashes = {r["hash"] for r in (rows or {}).values() if r.get("at_start") == "private" and r.get("hash")}
    names = {p for p in changed if is_private(p)}
    names.update(p for p in git.ignored_files(root, private_pathspecs()) if is_private(p))
    names.update(p for p in git.tracked_files(root, private_pathspecs()) if is_private(p))
    for name in names:
        file = Path(root, name)
        if paths.unsafe(root, name):
            continue  # never follow a link, even inside the repo
        if not is_link(file) and file.is_file() and file.stat().st_size > 0:
            hashes.add(fingerprint(file))
    return hashes


def is_copy_of_private(file, hashes):
    """True if `file` has exactly the same bytes as a private file. Hashes it; never reads it as text."""
    if not hashes or is_private(Path(file).name) or is_link(file) or not file.is_file():
        return False  # a private name is just private, not a "copy"
    size = file.stat().st_size
    return 0 < size <= SECRET_COPY_MAX_BYTES and fingerprint(file) in hashes


def seen_fingerprint(root, path):
    """What lineup saw of a changed file at a check: its fingerprint, "deleted", or None if not fingerprinted
    (private, behind a link, outside the repo, or big)."""
    file = Path(root, path)
    if paths.unsafe(root, path) or is_private(path) or is_link(file):
        return None
    if not os.path.lexists(file):
        return "deleted"
    if not file.is_file() or file.stat().st_size > BIG_FILE_BYTES:
        return None
    return fingerprint(file)


def _private_copy(path, kind):
    return Change(path=path, kind=kind, file_kind="private", private=True, copy_of_private=True)


def _kind(existed, exists_now):
    if not existed:
        return "added" if exists_now else None
    return "modified" if exists_now else "deleted"


def _compare(root, base, row, path, secret_hashes=frozenset()):
    """Find the "before" of one file (photo first, then git) and compare it with now.

    A file with the same bytes as a private file (`secret_hashes`) is treated as private and never read.
    """
    file = Path(root, path)
    reason = paths.unsafe(root, path)
    if reason == paths.OUTSIDE:
        return None  # never read anything outside the repo
    if reason and paths.link_in_folders(root, path):  # e.g. a folder replaced by a junction
        at_start = (row or {}).get("at_start")
        existed = at_start not in (None, "missing") or git.blob_size(root, (row or {}).get("base", base), path) is not None
        kind = _kind(existed, os.path.lexists(file)) or "modified"
        return Change(path=path, kind=kind, file_kind="private" if is_private(path) else file_kind(path),
                      private=is_private(path), symlink=True)
    if is_link(file):
        return _link_change(root, base, row, path, file)
    exists_now = file.is_file()
    at_start = (row or {}).get("at_start")

    if at_start == "symlink":  # was a link in the photo: nothing of it was read, so name only
        return Change(path=path, kind=_kind(True, exists_now), file_kind=file_kind(path),
                      private=is_private(path), symlink=True)

    if at_start == "private":  # only a fingerprint in the photo
        if exists_now and fingerprint(file) == row["hash"]:
            return None
        return Change(path=path, kind=_kind(True, exists_now), file_kind="private", private=True)

    if at_start == "big":  # only size and time in the photo
        if exists_now:
            stat = file.stat()
            if stat.st_size == row.get("size") and stat.st_mtime_ns == row.get("mtime_ns"):
                return None
        return Change(path=path, kind=_kind(True, exists_now), file_kind=file_kind(path), big=True)

    if at_start == "uncommitted":  # a saved copy in the photo
        data = store.read_copy(root, path) or b""
        kind = _kind(True, exists_now)
        if kind != "deleted" and is_copy_of_private(file, secret_hashes):
            return _private_copy(path, kind)
        return _build(root, path, kind, len(data), lambda: data)

    if at_start == "missing":  # already gone when the photo was taken
        kind = _kind(False, exists_now)
        if kind and is_copy_of_private(file, secret_hashes):
            return _private_copy(path, kind)
        return _build(root, path, kind, 0, lambda: b"") if kind else None

    rev = (row or {}).get("base", base)  # not in the photo: git has the "before"
    old_size = git.blob_size(root, rev, path)
    kind = _kind(old_size is not None, exists_now)
    if not kind:
        return None
    if kind != "deleted" and is_copy_of_private(file, secret_hashes):
        before = git.show_file(root, rev, path) if kind == "modified" else None
        if before is not None and hashlib.sha256(before).hexdigest() == fingerprint(file):
            return None  # unchanged since the photo (only hashed, never read as text)
        return _private_copy(path, kind)
    return _build(root, path, kind, old_size or 0, lambda: git.show_file(root, rev, path))


def committed_changes(root, old, new):
    """Files changed by the commits between `old` and `new`, each compared with its version at `old`."""
    found = []
    for path in git.files_between(root, old, new):
        old_size, new_size = git.blob_size(root, old, path), git.blob_size(root, new, path)
        kind = _kind(old_size is not None, new_size is not None)
        if not kind:
            continue
        change = _build(root, path, kind, old_size or 0, lambda p=path: git.show_file(root, old, p),
                        new_size or 0, lambda p=path: git.show_file(root, new, p))
        if change:
            found.append(change)
    return found


def find_changes(root, table=None):
    """Every file that changed since the photo (`table`), or since the last commit if there's no photo."""
    if table:
        base, rows = table["start_commit"], table["files"]
    else:
        base, rows = git.head_commit(root) or git.EMPTY_TREE, {}

    paths = {path for _, path in git.changed_files(root, base)}
    paths.update(git.untracked_files(root))
    paths.update(rows)
    if table:  # a hidden private file created after the photo
        paths.update(p for p in git.ignored_files(root, private_pathspecs()) if is_private(p))

    secret_hashes = private_fingerprints(root, rows, paths)
    found = []
    for path in sorted(paths):
        change = _compare(root, base, rows.get(path), path, secret_hashes)
        if change:
            found.append(change)
    return found


# ---- sorting into groups ----

def _what(change):
    if change.symlink:
        return "link, not followed"
    if change.private:
        return "private, contents not read"
    if change.big:
        return "big file, not read"
    if change.binary:
        return "binary file"
    return f"+{change.lines_added} -{change.lines_removed}"


def sort_into_groups(changes):
    """Group changes for display: {group: [(name, description, file path)]}."""
    from lineup.rules import assertions_removed  # here: rules is only needed for display

    groups = {"Additions": [], "Removals": [], "Edits": [], "Schema": []}
    for c in changes:
        def add(title, name, what, path=c.path):
            groups[title].append((name, what, path))

        test = c.file_kind == "test"
        not_read = c.private or c.big or c.binary or c.symlink
        if c.kind == "added":
            what = "new test file" if test else "new file"
            add("Additions", c.path, f"{what}, {_what(c)}" if not_read else what)
        elif c.kind == "deleted":
            what = "test file deleted" if test else "file deleted"
            add("Removals", c.path, f"{what}, {_what(c)}" if not_read else what)
        else:
            add("Edits", c.path, _what(c) if c.file_kind in ("code", "private") else f"{c.file_kind}, {_what(c)}")

        if c.kind != "deleted":
            for name in c.funcs_added:
                label = "test added" if test else "function added"
                add("Additions", name, f"{label} ({c.path})")
            for dep in c.deps_added:
                add("Additions", dep, f"new package ({c.path})")
        if c.kind != "added":
            for name in c.funcs_removed:
                label = "test removed" if test else "function removed"
                add("Removals", name, f"{label} ({c.path})")
        lost = assertions_removed(c)
        if lost:
            add("Removals", "assertion" if lost == 1 else "assertions", f"{lost} removed ({c.path})")

        if c.file_kind == "schema":
            add("Schema", c.path, c.kind)
    return groups


def format_groups(groups, base_label, box=None, ids=None):
    """The groups as one table: Group | ID | Name | What, with a line between groups.

    `ids` is {path: ID} (the same IDs as lineup risky); rows about a function, package or assertion
    show the ID of the file they're in.
    """
    ids = ids or {}
    blocks = []
    for title, items in groups.items():
        rows = items or [("nothing", "", None)]
        blocks.append([(title if i == 0 else "", "" if path is None else ids.get(path, "-"), name, what)
                       for i, (name, what, path) in enumerate(rows)])
    body = table.draw(("Group", "ID", "Name", "What"), blocks, (9, 4, 40, 50), box)
    return f"lineup diff (compared with {base_label})\n\n{body}"

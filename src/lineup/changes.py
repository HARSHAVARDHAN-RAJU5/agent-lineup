"""Finds what changed in the repo and sorts it into Additions, Removals, Edits and Schema."""

import difflib
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from lineup import functions, git, store, table
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
    lines_added: int = 0
    lines_removed: int = 0
    diff: str = ""  # never filled for private, big or binary files
    funcs_added: list = field(default_factory=list)
    funcs_removed: list = field(default_factory=list)
    deps_added: list = field(default_factory=list)


# ---- what kind of file is this (by path only) ----

_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
_TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|^conftest\.py$)")
_SCHEMA = re.compile(r"(^|/)(migrations?|alembic)/|\.sql$|(^|/)schema\.(prisma|rb|sql)$|(^|/)db/schema")
_DOCS = re.compile(r"\.(md|rst|adoc)$|^docs?/|(^|/)(readme|changelog|license)(\.\w+)?$", re.I)
_CONFIG = re.compile(r"\.(ya?ml|toml|ini|cfg|conf)$|(^|/)dockerfile|(^|/)docker-compose|(^|/)\.github/|(^|/)\.claude/", re.I)
_AUTH = re.compile(r"auth|login|session|password|oauth|payment|billing|checkout", re.I)


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
    if _AUTH.search(path):
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


def _build(root, path, kind, old_size, load_old):
    """Compare the before and after of one file. Returns None if nothing really changed."""
    change = Change(path=path, kind=kind, file_kind=file_kind(path))
    if change.file_kind == "private":
        change.private = True
        return change  # never read a private file
    file = Path(root, path)
    new_size = file.stat().st_size if kind != "deleted" else 0
    if max(old_size, new_size) > BIG_FILE_BYTES:
        change.big = True
        return change

    old = (load_old() or b"") if kind != "added" else b""
    new = file.read_bytes() if kind != "deleted" else b""
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
    change.funcs_added, change.funcs_removed = functions.function_changes(path, old_text, new_text)
    change.deps_added = functions.new_dependencies(path, old_text, new_text)
    return change


def _kind(existed, exists_now):
    if not existed:
        return "added" if exists_now else None
    return "modified" if exists_now else "deleted"


def _compare(root, base, row, path):
    """Find the "before" of one file (photo first, then git) and compare it with now."""
    file = Path(root, path)
    exists_now = file.is_file()
    at_start = (row or {}).get("at_start")

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
        return _build(root, path, kind, len(data), lambda: data)

    if at_start == "missing":  # already gone when the photo was taken
        kind = _kind(False, exists_now)
        return _build(root, path, kind, 0, lambda: b"") if kind else None

    rev = (row or {}).get("base", base)  # not in the photo: git has the "before"
    old_size = git.blob_size(root, rev, path)
    kind = _kind(old_size is not None, exists_now)
    if not kind:
        return None
    return _build(root, path, kind, old_size or 0, lambda: git.show_file(root, rev, path))


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

    found = []
    for path in sorted(paths):
        change = _compare(root, base, rows.get(path), path)
        if change:
            found.append(change)
    return found


# ---- sorting into groups ----

def _what(change):
    if change.private:
        return "private, contents not read"
    if change.big:
        return "big file, not read"
    if change.binary:
        return "binary file"
    return f"+{change.lines_added} -{change.lines_removed}"


def sort_into_groups(changes):
    """Group changes for display: {group: [(name, description)]}."""
    groups = {"Additions": [], "Removals": [], "Edits": [], "Schema": []}
    for c in changes:
        test = c.file_kind == "test"
        not_read = c.private or c.big or c.binary
        if c.kind == "added":
            what = "new test file" if test else "new file"
            groups["Additions"].append((c.path, f"{what}, {_what(c)}" if not_read else what))
        elif c.kind == "deleted":
            what = "test file deleted" if test else "file deleted"
            groups["Removals"].append((c.path, f"{what}, {_what(c)}" if not_read else what))
        else:
            groups["Edits"].append((c.path, _what(c) if c.file_kind in ("code", "private") else f"{c.file_kind}, {_what(c)}"))

        if c.kind != "deleted":
            for name in c.funcs_added:
                label = "test added" if test else "function added"
                groups["Additions"].append((name, f"{label} ({c.path})"))
            for dep in c.deps_added:
                groups["Additions"].append((dep, f"new package ({c.path})"))
        if c.kind != "added":
            for name in c.funcs_removed:
                label = "test removed" if test else "function removed"
                groups["Removals"].append((name, f"{label} ({c.path})"))

        if c.file_kind == "schema":
            groups["Schema"].append((c.path, c.kind))
    return groups


def format_groups(groups, base_label, box=None):
    """The groups as one table: Group | Name | What, with a line between groups."""
    blocks = []
    for title, items in groups.items():
        rows = items or [("nothing", "")]
        blocks.append([(title if i == 0 else "", name, what) for i, (name, what) in enumerate(rows)])
    body = table.draw(("Group", "Name", "What"), blocks, (9, 40, 50), box)
    return f"lineup diff (compared with {base_label})\n\n{body}"

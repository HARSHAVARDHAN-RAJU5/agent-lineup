"""Finds what changed in the repo and sorts it into Additions, Removals, Edits and Schema."""

import difflib
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from lineup import functions, git
from lineup.guard import is_private

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


def _old_size(root, base, path):
    return git.blob_size(root, base, path) or 0


def _new_size(root, path):
    file = Path(root, path)
    return file.stat().st_size if file.is_file() else 0


def _build(root, base, path, kind):
    change = Change(path=path, kind=kind, file_kind=file_kind(path))
    if change.file_kind == "private":
        change.private = True
        return change  # never read a private file
    old_size = _old_size(root, base, path) if kind != "added" else 0
    new_size = _new_size(root, path) if kind != "deleted" else 0
    if max(old_size, new_size) > BIG_FILE_BYTES:
        change.big = True
        return change

    old = git.show_file(root, base, path) if kind != "added" else b""
    new = Path(root, path).read_bytes() if kind != "deleted" else b""
    if _is_binary(old or b"") or _is_binary(new):
        change.binary = True
        return change

    old_text, new_text = _decode(old), _decode(new)
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, f"a/{path}", f"b/{path}", lineterm=""))
    body = diff[2:]  # skip the ---/+++ header
    change.lines_added = sum(1 for line in body if line.startswith("+"))
    change.lines_removed = sum(1 for line in body if line.startswith("-"))
    change.diff = "\n".join(diff)
    change.funcs_added, change.funcs_removed = functions.function_changes(path, old_text, new_text)
    change.deps_added = functions.new_dependencies(path, old_text, new_text)
    return change


def find_changes(root, base=None):
    """Every file that differs from `base` (default: the last commit), including untracked files."""
    base = base or git.head_commit(root) or git.EMPTY_TREE
    kinds = {"A": "added", "D": "deleted", "M": "modified"}
    found = {path: kinds.get(status, "modified") for status, path in git.changed_files(root, base)}
    for path in git.untracked_files(root):
        found.setdefault(path, "added")

    changes = []
    for path in sorted(found):
        change = _build(root, base, path, found[path])
        unchanged = (change.kind == "modified" and not change.private and not change.big
                     and not change.binary and not change.diff)
        if not unchanged:  # e.g. only line endings differ
            changes.append(change)
    return changes


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


def format_groups(groups, base_label):
    lines = [f"lineup diff (compared with {base_label})", ""]
    width = max((len(name) for items in groups.values() for name, _ in items), default=0)
    width = min(max(width, 10), 40)
    for title, items in groups.items():
        lines.append(title)
        if not items:
            lines.append("  nothing")
        for name, what in items:
            lines.append(f"  {name:<{width}}  {what}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

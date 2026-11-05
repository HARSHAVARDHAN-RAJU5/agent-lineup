"""Scores each change HIGH, MED or LOW with fixed rules. Same change, same score; no AI here."""

import posixpath
import re

from lineup import git
from lineup.guard import private_pathspecs

LEVELS = {"LOW": 1, "MED": 2, "HIGH": 3}

INSTALL_SCRIPTS = {"setup.py", "install.sh", "install.ps1"}
INSTALL_HOOKS = ('"preinstall"', '"install"', '"postinstall"')  # script entries in package.json
AGENT_AND_CI = (".github/workflows/", ".claude/", ".codex/")
MODEL_FILES = (".pt", ".pth", ".onnx", ".pkl", ".h5", ".ckpt", ".safetensors", ".joblib", ".tflite")
MANY_LINES_REMOVED = 100


def _changed_lines(change):
    """Added and removed lines of the diff, without the ---/+++ header."""
    return [line for line in change.diff.splitlines()[2:] if line[:1] in "+-"]


def _install_script(change):
    name = posixpath.basename(change.path).lower()
    if name in INSTALL_SCRIPTS or name.startswith(("postinstall", "preinstall")):
        return True
    if name == "package.json":
        return any(hook in line for line in _changed_lines(change) for hook in INSTALL_HOOKS)
    return False


def file_rules(change):
    """Every rule this change matches, as [(level, reason)]. Uses names and counts only."""
    flags = []
    path = change.path.lower()
    kind = change.file_kind

    # HIGH
    if change.copy_of_private:
        flags.append(("HIGH", "copy of a private file, contents not read"))
    elif change.private:
        flags.append(("HIGH", ".env or key file changed, contents not read"))
    if kind == "auth":
        flags.append(("HIGH", "auth or payment file changed"))
    if path.startswith(AGENT_AND_CI):
        flags.append(("HIGH", "CI or agent settings changed"))
    if _install_script(change):
        flags.append(("HIGH", "install script changed"))
    if kind == "schema":
        flags.append(("HIGH", "migration or database schema changed"))

    # MED
    if kind == "config" and not path.startswith(AGENT_AND_CI):
        flags.append(("MED", "config changed"))
    if kind == "dependencies":
        if change.deps_added:
            flags.append(("MED", "new package: " + ", ".join(change.deps_added)))
        else:
            flags.append(("MED", "dependency file changed"))
    if change.kind == "deleted" and kind != "test":  # deleted tests are scored with the test rules
        flags.append(("MED", "file deleted"))
    if change.lines_removed >= MANY_LINES_REMOVED:
        flags.append(("MED", f"{change.lines_removed} lines removed"))
    if change.big:
        flags.append(("MED", "big file changed, can't be read"))
    elif path.endswith(MODEL_FILES):
        flags.append(("MED", "model file changed"))

    # LOW
    if kind == "lockfile":
        flags.append(("LOW", "lockfile changed"))
    if kind == "docs":
        flags.append(("LOW", "docs changed"))
    if change.kind == "added" and kind != "test" and not flags:
        flags.append(("LOW", "new file"))
    if change.kind == "modified" and kind == "code":
        flags.append(("LOW", "code edited"))
    if change.kind != "deleted":
        word = "test added" if kind == "test" else "function added"
        for name in change.funcs_added:
            flags.append(("LOW", f"{word}: {name}"))

    if not flags and kind != "test":  # test files always get a test rule below
        flags.append(("LOW", "changed"))
    return flags


# ---- test files ----

_ASSERT = re.compile(r"\bassert\w*\b|\bexpect\s*\(|\bt\.(?:Error|Errorf|Fatal|Fatalf)\s*\(")
_SKIP = re.compile(
    r"pytest\.mark\.skip|pytest\.skip\s*\(|unittest\.skip|\.skipTest\s*\("
    r"|\b(?:it|test|describe)\.skip\s*\(|\bx(?:it|test|describe)\s*\(|\bt\.Skip(?:Now|f)?\s*\("
)
_TEST_PARTS = re.compile(r"^test_|_test$|\.(?:test|spec)$")
_COMMENT = ("#", "//", "/*", "*")


def _is_assert(line):
    """An assertion line. A commented-out one (# assert x) doesn't count, so commenting it out = removing it."""
    return not line.lstrip().startswith(_COMMENT) and bool(_ASSERT.search(line))


def _stem(path):
    """File name without folders and extension: tests/test_auth.py -> test_auth."""
    return posixpath.splitext(posixpath.basename(path))[0].lower()


def code_name(test_path):
    """The code file a test belongs to, by name: test_auth.py, auth_test.go, auth.test.js -> auth."""
    return _TEST_PARTS.sub("", _stem(test_path))


def linked_code(test_change, all_changes):
    """Changed non-test files with the same name as the test's code: test_auth.py -> auth.py."""
    name = code_name(test_change.path)
    return [c.path for c in all_changes
            if c.file_kind != "test" and c.path != test_change.path and _stem(c.path) == name]


def test_rules(change, all_changes):
    """Rules for test files only, as [(level, reason)]. Other files get []."""
    if change.file_kind != "test":
        return []
    if change.kind == "deleted":
        return [("HIGH", "test file deleted")]

    flags = [("HIGH", f"test deleted: {name}") for name in change.funcs_removed]
    lines = _changed_lines(change)
    removed = [line[1:] for line in lines if line.startswith("-")]
    added = [line[1:] for line in lines if line.startswith("+")]

    asserts_removed = sum(1 for line in removed if _is_assert(line))
    asserts_added = sum(1 for line in added if _is_assert(line))
    if asserts_removed > asserts_added:
        flags.append(("HIGH", "assertion removed"))
    if any(_SKIP.search(line) for line in added):
        flags.append(("HIGH", "skip added"))

    if asserts_removed and asserts_added:  # an assert line was changed
        code = linked_code(change, all_changes)
        if code:
            flags.append(("MED", f"possible weakened test, check this ({', '.join(code)} changed too)"))

    if not any(level in ("HIGH", "MED") for level, _ in flags):
        flags.append(("MED", "new test file" if change.kind == "added" else "test edited"))
    return flags


# ---- deleted functions ----

def added_in(all_changes):
    """Where each function name was added in this change: {name: [paths]}."""
    found = {}
    for c in all_changes:
        if c.kind != "deleted":
            for name in c.funcs_added:
                found.setdefault(name, []).append(c.path)
    return found


def function_rules(change, all_changes, root=None):
    """Deleted functions in a non-test file: moved (LOW), still used (HIGH) or just deleted (LOW)."""
    if change.file_kind == "test" or change.kind == "added":
        return []
    flags = []
    moved_to = added_in(all_changes)
    for name in change.funcs_removed:
        if name.startswith("__") and name.endswith("__"):
            continue  # __init__ and friends exist in every class
        elsewhere = [p for p in moved_to.get(name, []) if p != change.path]
        if elsewhere:
            flags.append(("LOW", f"function moved: {name} ({change.path} -> {', '.join(elsewhere)})"))
            continue
        users = git.files_using(root, name, exclude=private_pathspecs()) if root else []
        if users:
            shown = ", ".join(users[:3]) + (", ..." if len(users) > 3 else "")
            flags.append(("HIGH", f"function deleted but still used: {name} ({shown})"))
        else:
            flags.append(("LOW", f"function deleted: {name}"))
    return flags


# ---- possible breakage (a guess, always MED) ----

BREAKAGE_MIN_REMOVED = 10
BREAKAGE_RATIO = 3
SMALL_NEW_FILE = 10


def breakage_rules(change, all_changes):
    """Working code removed for a small fix, or a small new file added next to a test."""
    if change.file_kind != "code":
        return []
    removed, added = change.lines_removed, change.lines_added
    if change.kind == "modified" and removed >= BREAKAGE_MIN_REMOVED and removed >= BREAKAGE_RATIO * added:
        return [("MED", f"possible breakage: {removed} lines removed, {added} added")]
    if change.kind == "added" and added <= SMALL_NEW_FILE:
        name = _stem(change.path)
        tests = [c.path for c in all_changes if c.file_kind == "test" and code_name(c.path) == name]
        if tests:
            return [("MED", f"possible breakage: small new file next to {', '.join(tests)}")]
    return []


def rules_for(change, all_changes, root=None):
    """Every rule one change matches. `root` lets the deleted-function check search the repo."""
    return (file_rules(change) + test_rules(change, all_changes)
            + function_rules(change, all_changes, root) + breakage_rules(change, all_changes))


def score(flags):
    """The highest level among the flags: HIGH beats MED beats LOW."""
    return max((level for level, _ in flags), key=LEVELS.get, default="LOW")

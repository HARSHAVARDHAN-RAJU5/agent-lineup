"""Scores each change HIGH, MED or LOW with fixed rules. Same change, same score; no AI here."""

import posixpath

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
    if change.private:
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
    if change.kind == "added" and not flags:
        flags.append(("LOW", "new file"))
    if change.kind == "modified" and kind == "code":
        flags.append(("LOW", "code edited"))
    if change.kind != "deleted":
        for name in change.funcs_added:
            flags.append(("LOW", f"function added: {name}"))

    if not flags:
        flags.append(("LOW", "changed"))
    return flags


def score(flags):
    """The highest level among the flags: HIGH beats MED beats LOW."""
    return max((level for level, _ in flags), key=LEVELS.get, default="LOW")

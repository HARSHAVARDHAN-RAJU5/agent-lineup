"""Thin wrappers around the `git` command. The only module that runs git."""

import subprocess
from pathlib import Path

# Git's well-known empty tree: lets a repo with no commits be diffed like any other.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(Exception):
    pass


def _run(args, cwd, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(message or f"git {' '.join(args)} failed")
    return result


def _split_z(output):
    return [p for p in output.decode("utf-8", "replace").split("\0") if p]


def repo_root(cwd="."):
    """Top folder of the repo containing `cwd`."""
    try:
        out = _run(["rev-parse", "--show-toplevel"], cwd).stdout
    except (GitError, FileNotFoundError) as e:
        raise GitError("not inside a git repository") from e
    return Path(out.decode("utf-8", "replace").strip())


def git_dir(root):
    """The repo's .git folder (also right for git worktrees)."""
    out = _run(["rev-parse", "--absolute-git-dir"], root).stdout
    return Path(out.decode("utf-8", "replace").strip())


def head_commit(root):
    """Current commit ID, or None if the repo has no commits yet."""
    result = _run(["rev-parse", "--verify", "--quiet", "HEAD"], root, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip()


def changed_files(root, base):
    """Tracked files that differ between `base` and the working tree, as (status, path).

    Status is A (added), D (deleted) or M (modified). Renames show as D + A.
    """
    out = _run(["diff", "--name-status", "--no-renames", "-z", base, "--"], root).stdout
    parts = _split_z(out)
    pairs = []
    for status, path in zip(parts[0::2], parts[1::2]):
        status = status[0]
        pairs.append(("M" if status == "T" else status, path))
    return pairs


def untracked_files(root):
    """New files git has never seen, minus ignored ones."""
    out = _run(["ls-files", "--others", "--exclude-standard", "-z"], root).stdout
    return _split_z(out)


def show_file(root, rev, path):
    """File contents at `rev` as bytes, or None if it didn't exist there."""
    result = _run(["show", f"{rev}:{path}"], root, check=False)
    return result.stdout if result.returncode == 0 else None


def checkout_bytes(root, rev, path):
    """File contents at `rev` exactly as git would write them on checkout (core.autocrlf, .gitattributes).

    None if it didn't exist there.
    """
    result = _run(["cat-file", "--filters", f"{rev}:{path}"], root, check=False)
    return result.stdout if result.returncode == 0 else None


def file_mode(root, rev, path):
    """Git's mode for `path` at `rev`, like "100644" or "100755" (executable). None if not there."""
    result = _run(["ls-tree", "-z", rev, "--", path], root, check=False)
    entry = result.stdout.decode("utf-8", "replace").split("\0")[0]
    return entry.split(" ", 1)[0] if result.returncode == 0 and entry else None


def staged_differs(root):
    """Files whose staged copy (index) differs from both the last commit and the file on disk."""
    out = _run(["status", "--porcelain=v1", "-z", "--untracked-files=no"], root).stdout.decode("utf-8", "replace")
    paths = set()
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        if x in "RC":
            i += 1  # the old name follows a rename/copy
        if x not in " ?" and y not in " ?":
            paths.add(path)
    return paths


def blob_size(root, rev, path):
    """Size in bytes of a file at `rev`, or None if it didn't exist there."""
    result = _run(["cat-file", "-s", f"{rev}:{path}"], root, check=False)
    return int(result.stdout) if result.returncode == 0 else None


def files_between(root, old, new):
    """Files changed by the commits between `old` and `new`."""
    out = _run(["diff", "--name-only", "--no-renames", "-z", old, new, "--"], root).stdout
    return _split_z(out)


def files_using(root, word, exclude=()):
    """Files (tracked or new) that contain `word` as a whole word. Names only, never the lines.

    `exclude` is a list of git patterns for files that must not be searched.
    """
    args = ["grep", "-l", "-w", "-F", "-I", "--untracked", "-z", "-e", word, "--", "."]
    args += [f":(exclude,{p[2:]}" if p.startswith(":(") else f":(exclude){p}" for p in exclude]
    result = _run(args, root, check=False)
    return _split_z(result.stdout) if result.returncode == 0 else []


def is_tracked(root, path):
    """True if git tracks `path` (it's in the index)."""
    return _run(["ls-files", "--error-unmatch", "--", path], root, check=False).returncode == 0


def is_ignored(root, path):
    """True if .gitignore, .git/info/exclude or the global excludes file ignore `path`."""
    return _run(["check-ignore", "-q", "--no-index", "--", path], root, check=False).returncode == 0


def tracked_files(root, only=None):
    """Files git tracks (names only). `only` is a list of git patterns."""
    args = ["ls-files", "-z"]
    if only:
        args += ["--", *only]
    return _split_z(_run(args, root).stdout)


def ignored_files(root, only=None):
    """Files hidden by .gitignore, including files inside hidden folders (names only).

    `only` is a list of git patterns; when given, git returns just the matching names.
    """
    args = ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]
    if only:
        args += ["--", *only]
    return _split_z(_run(args, root).stdout)

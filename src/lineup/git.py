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


def blob_size(root, rev, path):
    """Size in bytes of a file at `rev`, or None if it didn't exist there."""
    result = _run(["cat-file", "-s", f"{rev}:{path}"], root, check=False)
    return int(result.stdout) if result.returncode == 0 else None

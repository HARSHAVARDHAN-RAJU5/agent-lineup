"""Path safety: never read, copy, write or delete anything behind a link or outside the repo."""

import os
import stat
from pathlib import Path, PurePosixPath

OUTSIDE = "outside the repo"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def is_link(path):
    """True for a symlink, or a Windows junction (Python's islink misses junctions)."""
    path = os.fspath(path)
    if os.path.islink(path):
        return True
    if os.name == "nt":
        try:
            return bool(os.lstat(path).st_file_attributes & _REPARSE)
        except (OSError, AttributeError):
            return False
    return False


def _within(child, parent):
    child, parent = os.path.normcase(child), os.path.normcase(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives on Windows
        return False


def unsafe(root, path):
    """Why `path` must not be touched, or None if it's safe.

    Refuses `..` and absolute paths, anything whose own folders (or itself) are a link or junction,
    and anything whose real location is outside the repo.
    """
    posix = PurePosixPath(path)
    if not posix.parts or posix.is_absolute() or ".." in posix.parts or Path(path).is_absolute():
        return OUTSIDE
    current = Path(root)
    for part in posix.parts:
        current = current / part
        if not os.path.lexists(current):
            break
        if is_link(current):
            linked = PurePosixPath(*posix.parts[:len(current.relative_to(root).parts)]).as_posix()
            return "symlink, not followed" if linked == posix.as_posix() else f"inside a linked folder ({linked}), not followed"
    if not _within(os.path.realpath(Path(root, path)), os.path.realpath(root)):
        return OUTSIDE
    return None


def link_in_folders(root, path):
    """True if one of the folders on the way to `path` is a link (the file itself isn't checked)."""
    reason = unsafe(root, path)
    return bool(reason) and reason.startswith("inside a linked folder")

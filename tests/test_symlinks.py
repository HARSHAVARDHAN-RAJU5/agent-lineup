"""Fix 2: symlinks are never followed, on any platform."""

import os

import pytest

from lineup import changes, snapshot, store
from lineup.changes import find_changes
from lineup.guard import ai_input, build_message


@pytest.fixture
def fake_link(monkeypatch):
    """Windows here can't make real symlinks, so pretend `notes.txt` is a link to `.env`.

    The file on disk holds the secret, which is what reading "through" a link would return.
    """
    def is_link(file):
        return os.path.basename(str(file)) == "notes.txt" and os.path.exists(file)
    monkeypatch.setattr(changes, "is_link", is_link)
    monkeypatch.setattr(changes, "read_link", lambda file: b".env")


def test_link_is_sent_by_name_only(repo, fake_link):
    repo.write(".gitignore", ".env\n")
    repo.write("app.py", "x = 1\n")
    repo.commit()
    snapshot.take(repo.root)
    repo.write(".env", "SECRET=abc123\n")
    repo.write("notes.txt", "SECRET=abc123\n")  # what following the link would read
    found = {c.path: c for c in find_changes(repo.root, store.load_table(repo.root))}
    link = found["notes.txt"]
    assert link.symlink and link.diff == "" and link.kind == "added"
    message = build_message(ai_input(list(found.values()), {})[0])
    assert "abc123" not in message
    assert "--- notes.txt (LOW): link, not followed" in message


def test_photo_keeps_only_where_a_link_points(repo, fake_link):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("notes.txt", "SECRET=abc123\n")
    table = snapshot.take(repo.root)
    assert table["files"]["notes.txt"]["at_start"] == "symlink"
    assert store.read_copy(repo.root, "notes.txt") is None  # no copy of the target saved
    assert find_changes(repo.root, table) == []  # same link: no change


def test_link_pointing_somewhere_else_is_a_change(repo, fake_link, monkeypatch):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("notes.txt", "x\n")
    table = snapshot.take(repo.root)
    monkeypatch.setattr(changes, "read_link", lambda file: b"keys/server.pem")
    found = find_changes(repo.root, table)
    assert [(c.path, c.kind, c.symlink) for c in found] == [("notes.txt", "modified", True)]


def _can_symlink(tmp_path):
    try:
        os.symlink("x", tmp_path / "probe")
        return True
    except (OSError, NotImplementedError):
        return False


def test_real_symlink_to_env(repo, tmp_path_factory):
    """Runs on Mac/Linux (and Windows with Developer Mode); skipped where symlinks can't be made."""
    if not _can_symlink(tmp_path_factory.mktemp("probe")):
        pytest.skip("this machine can't create symlinks")
    repo.write(".gitignore", ".env\n")
    repo.write("app.py", "x = 1\n")
    repo.commit()
    snapshot.take(repo.root)
    repo.write(".env", "SECRET=abc123\n")
    os.symlink(".env", repo.root / "notes.txt")
    found = find_changes(repo.root, store.load_table(repo.root))
    message = build_message(ai_input(found, {})[0])
    assert "abc123" not in message
    assert "notes.txt (LOW): link, not followed" in message

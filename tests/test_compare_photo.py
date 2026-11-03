"""Step 4: changes are compared with the photo, not just the last commit."""

import pytest

from lineup import changes, snapshot, store
from lineup.changes import find_changes
from lineup.cli import main


@pytest.fixture
def project(repo):
    """app.py committed; notes.py has your unfinished line A; .env hidden by .gitignore."""
    repo.write(".gitignore", ".env\n")
    repo.write("app.py", "TIMEOUT = 30\n")
    repo.write("notes.py", "start\n")
    repo.write("old.py", "x = 1\n")
    repo.commit("start")
    repo.write("notes.py", "start\nline A\n")
    repo.write(".env", "SECRET_KEY=abc123\n")
    repo.delete("old.py")  # you deleted it before the session
    return repo


def claude_edits(repo):
    repo.write("app.py", "TIMEOUT = 1440\n")
    repo.write("notes.py", "start\nline A\nline B\n")
    repo.write(".env", "SECRET_KEY=xyz789\n")
    repo.write("cache.py", "def get(key):\n    return None\n")


def by_path(found):
    return {c.path: c for c in found}


def test_only_claudes_line_shows(project):
    table = snapshot.take(project.root)
    claude_edits(project)
    found = by_path(find_changes(project.root, table))

    assert set(found) == {"app.py", "notes.py", ".env", "cache.py"}
    assert (found["notes.py"].lines_added, found["notes.py"].lines_removed) == (1, 0)
    assert "+line B" in found["notes.py"].diff
    assert "+line A" not in found["notes.py"].diff
    assert (found["app.py"].lines_added, found["app.py"].lines_removed) == (1, 1)
    assert found["cache.py"].kind == "added"


def test_nothing_changed_since_photo(project):
    table = snapshot.take(project.root)
    assert find_changes(project.root, table) == []


def test_hidden_env_change_caught_without_reading(project):
    table = snapshot.take(project.root)
    claude_edits(project)
    env = by_path(find_changes(project.root, table))[".env"]
    assert env.private and env.kind == "modified" and env.diff == ""


def test_new_hidden_env_after_photo(repo):
    repo.write(".gitignore", ".env\n")
    repo.commit()
    table = snapshot.take(repo.root)
    repo.write(".env", "SECRET_KEY=abc123\n")
    env = by_path(find_changes(repo.root, table))[".env"]
    assert env.private and env.kind == "added"


def test_file_deleted_before_session_not_blamed(project):
    table = snapshot.take(project.root)
    assert "old.py" not in by_path(find_changes(project.root, table))


def test_file_brought_back_shows_as_new(project):
    table = snapshot.take(project.root)
    project.write("old.py", "x = 2\n")
    assert by_path(find_changes(project.root, table))["old.py"].kind == "added"


def test_unfinished_file_deleted_by_claude(project):
    table = snapshot.take(project.root)
    project.delete("notes.py")
    notes = by_path(find_changes(project.root, table))["notes.py"]
    assert notes.kind == "deleted" and notes.lines_removed == 2


def test_big_file_change_caught_without_reading(project, monkeypatch):
    monkeypatch.setattr(changes, "BIG_FILE_BYTES", 40)
    project.write("model.bin", "x" * 50)
    table = snapshot.take(project.root)
    assert "model.bin" not in by_path(find_changes(project.root, table))
    project.write("model.bin", "y" * 60)
    model = by_path(find_changes(project.root, table))["model.bin"]
    assert model.big and model.kind == "modified" and model.diff == ""


def test_unchanged_binary_copy_not_listed(project):
    (project.root / "logo.png").write_bytes(b"\x89PNG\0\0data")
    table = snapshot.take(project.root)
    assert find_changes(project.root, table) == []


# ---- lineup diff ----

def test_diff_uses_photo(project, monkeypatch, capsys):
    monkeypatch.chdir(project.root)
    snapshot.take(project.root)
    claude_edits(project)
    assert main(["diff"]) == 0
    out = capsys.readouterr().out
    assert "compared with photo taken" in out
    assert "notes.py" in out and "+1 -0" in out
    assert "abc123" not in out and "xyz789" not in out


def test_diff_without_photo_takes_one(project, monkeypatch, capsys):
    monkeypatch.chdir(project.root)
    claude_edits(project)
    assert not store.has_snapshot(project.root)

    assert main(["diff"]) == 0
    first = capsys.readouterr().out
    assert "compared with last commit" in first
    assert "+2 -0" in first  # line A and line B together: can't tell them apart yet
    assert "one was taken now" in first
    assert store.has_snapshot(project.root)

    assert main(["diff"]) == 0
    second = capsys.readouterr().out
    assert "no changes since the photo" in second
    assert "one was taken now" not in second

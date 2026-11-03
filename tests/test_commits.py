"""Step 5: changed files get fixed IDs; new commits are flagged first, then taken off the undo list."""

import pytest

from lineup import git, snapshot, store
from lineup.changes import find_changes
from lineup.cli import main


@pytest.fixture
def project(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("app.py", "TIMEOUT = 30\n")
    repo.write("notes.py", "start\n")
    repo.commit("start")
    repo.write("notes.py", "start\nline A\n")
    repo.write(".env", "SECRET_KEY=abc123\n")
    snapshot.take(repo.root)
    # Claude's edits
    repo.write("app.py", "TIMEOUT = 1440\n")
    repo.write("notes.py", "start\nline A\nline B\n")
    repo.write(".env", "SECRET_KEY=xyz789\n")
    repo.write("cache.py", "def get(key):\n    return None\n")
    return repo


def check(repo):
    """What `lineup diff` does with a photo: commits, then compare, then record."""
    table = store.load_table(repo.root)
    flagged = snapshot.check_commits(repo.root, table)
    found = find_changes(repo.root, table)
    snapshot.record_check(repo.root, table, found)
    return flagged, found, store.load_table(repo.root)


def ids(table):
    return {path: row["id"] for path, row in table["files"].items() if "id" in row}


def test_changed_files_get_ids(project):
    _, found, table = check(project)
    assert ids(table) == {".env": 1, "app.py": 2, "cache.py": 3, "notes.py": 4}
    assert table["next_id"] == 5
    assert all(table["files"][c.path]["status"] == "changed" for c in found)


def test_ids_never_change_and_new_files_get_next(project):
    check(project)
    project.write("utils.py", "x = 1\n")
    _, _, table = check(project)
    assert ids(table) == {".env": 1, "app.py": 2, "cache.py": 3, "notes.py": 4, "utils.py": 5}


def test_file_put_back_is_marked_unchanged_but_keeps_id(project):
    check(project)
    project.write("app.py", "TIMEOUT = 30\n")
    _, _, table = check(project)
    assert table["files"]["app.py"] == {"id": 2, "status": "unchanged"}


def test_no_new_commit_flags_nothing(project):
    flagged, _, _ = check(project)
    assert flagged == []


def test_commit_two_of_four(project):
    check(project)
    project.git("add", "app.py", "notes.py")
    project.git("commit", "-q", "-m", "partial")
    head = git.head_commit(project.root)

    flagged, found, table = check(project)

    assert flagged == [("app.py", "code"), ("notes.py", "code")]
    assert table["last_seen_commit"] == head
    assert table["files"]["app.py"] == {"id": 2, "status": "committed", "base": head}
    assert table["files"]["notes.py"] == {"id": 4, "status": "committed", "base": head}
    assert store.read_copy(project.root, "notes.py") is None  # copy deleted
    assert {c.path for c in found} == {".env", "cache.py"}  # the other two stay


def test_flag_comes_before_taking_off(project):
    check(project)
    project.git("add", "notes.py")
    project.git("commit", "-q", "-m", "notes")
    check(project)
    log = store.read_log(project.root)
    flagged_at = next(i for i, line in enumerate(log) if "notes.py (code) flagged" in line)
    taken_at = next(i for i, line in enumerate(log) if "notes.py taken off the undo list" in line)
    assert flagged_at < taken_at


def test_committed_file_edited_again_compares_with_commit(project):
    check(project)
    project.git("add", "app.py")
    project.git("commit", "-q", "-m", "app")
    check(project)
    project.write("app.py", "TIMEOUT = 60\n")
    _, found, table = check(project)
    app = {c.path: c for c in found}["app.py"]
    assert (app.lines_added, app.lines_removed) == (1, 1)
    assert "-TIMEOUT = 1440" in app.diff  # before = the commit, not the photo
    assert table["files"]["app.py"]["id"] == 2


def test_diary_has_names_but_no_code(project):
    check(project)
    log = "\n".join(store.read_log(project.root))
    assert "checked: 4 files changed (.env, app.py, cache.py, notes.py)" in log
    assert "TIMEOUT" not in log and "line B" not in log and "abc123" not in log and "xyz789" not in log


def test_diff_shows_committed_files(project, monkeypatch, capsys):
    monkeypatch.chdir(project.root)
    main(["diff"])
    capsys.readouterr()
    project.git("add", "app.py")
    project.git("commit", "-q", "-m", "app")
    main(["diff"])
    out = capsys.readouterr().out
    assert out.startswith("Committed since last check: app.py (code)")
    assert "app.py " not in out.split("Edits", 1)[1]

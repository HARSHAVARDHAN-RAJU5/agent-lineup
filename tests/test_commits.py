"""Step 5: changed files get fixed IDs; new commits are flagged first, then taken off the undo list."""

import json

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

    assert [(c.path, c.file_kind) for c in flagged] == [("app.py", "code"), ("notes.py", "code")]
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


def test_committed_file_is_compared_with_its_version_before_the_commit(project):
    check(project)
    project.git("add", "app.py")
    project.git("commit", "-q", "-m", "app")
    project.write("app.py", "TIMEOUT = 60\n")  # edited again after the commit: not part of the commit
    flagged, _, _ = check(project)
    app = flagged[0]
    assert app.path == "app.py" and app.kind == "modified"
    assert "-TIMEOUT = 30" in app.diff and "+TIMEOUT = 1440" in app.diff


def test_risky_scores_committed_files(repo, monkeypatch, capsys):
    repo.write("auth/tokens.py", "TTL = 5\n")
    repo.write("notes.py", "x = 1\n")
    repo.commit("start")
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    repo.write("auth/tokens.py", "TTL = 50\n")
    repo.git("add", "auth/tokens.py")
    repo.git("commit", "-q", "-m", "tokens")
    repo.write("notes.py", "x = 2\n")
    commit = git.head_commit(repo.root)
    capsys.readouterr()

    def risky():
        main(["risky"])
        out = capsys.readouterr().out
        main_table, _, committed = out.partition("Committed (lineup can't roll these back; use git to undo them)")
        return main_table, committed

    for _ in range(2):  # shown on every run, not just the first
        main_table, committed = risky()
        assert "notes.py" in main_table and "auth/tokens.py" not in main_table
        tokens = next(line for line in committed.splitlines() if "auth/tokens.py" in line)
        assert commit[:7] in tokens and "HIGH" in tokens and "auth or payment file changed" in tokens

    table = store.load_table(repo.root)
    assert table["files"]["auth/tokens.py"]["status"] == "committed"
    assert table["committed"] == [{"commit": commit, "path": "auth/tokens.py", "risk": "HIGH",
                                   "reasons": ["auth or payment file changed"]}]
    assert "TTL" not in json.dumps(table)  # no code saved

    main(["hook", "start", "--fresh"])  # a fresh photo clears them
    capsys.readouterr()
    assert "Committed" not in risky()[1] and "auth/tokens.py" not in "".join(risky())


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

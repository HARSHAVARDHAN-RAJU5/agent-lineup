"""Fixes after the Phase 2 tests: no-photo warning, new session keeps uncommitted changes, --fresh, grammar."""

import pytest

from lineup import snapshot, store
from lineup.changes import find_changes
from lineup.cli import main

NO_PHOTO = ("No snapshot from the session start. These changes may include your own edits, "
            "and they can't be rolled back. Tracking from now on.")


@pytest.fixture
def project(repo, monkeypatch):
    repo.write("a.py", "a = 1\n")
    repo.write("b.py", "b = 1\n")
    repo.write("c.py", "c = 1\n")
    repo.commit("start")
    monkeypatch.chdir(repo.root)
    return repo


def output(capsys, argv):
    assert main(argv) == 0
    return capsys.readouterr().out


# ---- Problem 1: no photo ----

def test_no_photo_warning(project, capsys):
    project.write("a.py", "a = 2\n")
    out = output(capsys, ["diff"])
    assert out.rstrip().endswith(NO_PHOTO)
    assert store.has_snapshot(project.root)


def test_no_photo_warning_even_with_no_changes(project, capsys):
    out = output(capsys, ["diff"])
    assert NO_PHOTO in out


# ---- Problem 2: new session with a photo already there ----

def test_new_session_keeps_photo_with_uncommitted_changes(project, capsys):
    output(capsys, ["hook", "start"])
    project.write("a.py", "a = 2  # session 1\n")
    project.write("b.py", "b = 2  # session 1\n")
    first = store.load_table(project.root)

    out = output(capsys, ["hook", "start"])

    assert out.strip() == ("Keeping the earlier snapshot: 2 uncommitted files from before this session. "
                           "Some may be your own edits. Commit them, or run lineup hook start --fresh to start over.")
    table = store.load_table(project.root)
    assert table["start_commit"] == first["start_commit"]
    assert table["taken_at"] == first["taken_at"]
    assert table["files"]["a.py"] == {"id": 1, "status": "changed"}  # still flagged, still on the undo list
    assert table["files"]["b.py"] == {"id": 2, "status": "changed"}
    assert {c.path for c in find_changes(project.root, table)} == {"a.py", "b.py"}


def test_kept_message_says_file_for_one(project, capsys):
    output(capsys, ["hook", "start"])
    project.write("a.py", "a = 2\n")
    assert "1 uncommitted file from before" in output(capsys, ["hook", "start"])


def test_kept_photo_keeps_original_copy(project, capsys):
    project.write("notes.py", "your line\n")  # unfinished before session 1
    output(capsys, ["hook", "start"])
    project.write("notes.py", "your line\nclaude line\n")
    output(capsys, ["hook", "start"])
    assert store.read_copy(project.root, "notes.py") == b"your line\n"


def test_new_session_takes_new_photo_when_nothing_left(project, capsys):
    output(capsys, ["hook", "start"])
    project.write("a.py", "a = 2\n")
    project.commit("a")
    first = store.load_table(project.root)

    out = output(capsys, ["hook", "start"])

    assert out.startswith("lineup: photo taken at")
    table = store.load_table(project.root)
    assert table["start_commit"] != first["start_commit"]
    assert table["files"] == {}


def test_new_session_after_partial_commit(project, capsys):
    output(capsys, ["hook", "start"])
    project.write("a.py", "a = 2\n")
    project.write("b.py", "b = 2\n")
    project.write("c.py", "c = 2\n")
    project.git("add", "a.py", "b.py")
    project.git("commit", "-q", "-m", "a and b")

    out = output(capsys, ["hook", "start"])

    assert "Keeping the earlier snapshot: 1 uncommitted file" in out
    table = store.load_table(project.root)
    assert table["files"]["a.py"]["status"] == "committed"
    assert table["files"]["b.py"]["status"] == "committed"
    assert table["files"]["c.py"]["status"] == "changed"


def test_resume_and_compact_still_keep_photo(project):
    first = snapshot.take(project.root)
    project.write("a.py", "a = 2\n")
    assert snapshot.take(project.root, keep=True) == first


# ---- --fresh ----

def test_fresh_always_takes_new_photo(project, capsys):
    output(capsys, ["hook", "start"])
    project.write("a.py", "a = 2  # session 1\n")

    out = output(capsys, ["hook", "start", "--fresh"])

    assert out.startswith("lineup: photo taken at")
    table = store.load_table(project.root)
    assert table["files"] == {"a.py": {"at_start": "uncommitted", "copy": True}}
    assert store.read_copy(project.root, "a.py") == b"a = 2  # session 1\n"


# ---- grammar ----

def test_diary_says_1_file_changed(project, capsys):
    output(capsys, ["hook", "start"])
    project.write("a.py", "a = 2\n")
    output(capsys, ["diff"])
    log = "\n".join(store.read_log(project.root))
    assert "checked: 1 file changed (a.py)" in log
    assert "1 files" not in log

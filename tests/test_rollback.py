"""Phase 6: lineup rollback. Step 1: deciding what undoing a file means (nothing on disk changes)."""

import pytest

from lineup import snapshot, store
from lineup.changes import BIG_FILE_BYTES, find_changes
from lineup.rollback import (REFUSE_BIG, REFUSE_COMMITTED, REFUSE_COPY, REFUSE_PRIVATE, UnknownId,
                             plan_undo)


@pytest.fixture
def session(repo):
    """Photo taken, then the agent's edits, then a `lineup diff`-style check that gives IDs."""
    repo.write(".gitignore", ".env\n")
    repo.write("auth/session.py", "TIMEOUT = 30\n")
    repo.write("old.py", "OLD = 1\n")
    repo.write(".env", "SECRET=abc\n")
    repo.commit()
    repo.write("notes.py", "start\n")  # uncommitted at the photo: gets a saved copy
    snapshot.take(repo.root)
    # the agent's edits
    repo.write("auth/session.py", "TIMEOUT = 1440\n")
    repo.write("notes.py", "start\nagent line\n")
    repo.write("utils/cache.py", "def get(key):\n    return None\n")
    repo.delete("old.py")
    repo.write(".env", "SECRET=changed\n")
    return repo


def check(repo):
    table = store.load_table(repo.root)
    snapshot.check_commits(repo.root, table)
    snapshot.record_check(repo.root, table, find_changes(repo.root, table))
    return store.load_table(repo.root)


def undo_for(repo, table, path):
    return plan_undo(repo.root, table, table["files"][path]["id"])


def disk(repo):
    return {p.relative_to(repo.root).as_posix(): p.read_bytes()
            for p in repo.root.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_each_kind_of_change(session):
    table = check(session)
    before = disk(session)

    edited = undo_for(session, table, "auth/session.py")
    assert (edited.action, edited.before) == ("restore", b"TIMEOUT = 30\n")  # from git

    copy = undo_for(session, table, "notes.py")
    assert (copy.action, copy.before) == ("restore", b"start\n")  # from the saved copy

    added = undo_for(session, table, "utils/cache.py")
    assert added.action == "delete"

    deleted = undo_for(session, table, "old.py")
    assert (deleted.action, deleted.before) == ("bring back", b"OLD = 1\n")

    env = undo_for(session, table, ".env")
    assert (env.action, env.reason) == ("refuse", REFUSE_PRIVATE)

    assert disk(session) == before  # step 1 only reads


def test_committed_file_is_refused(session):
    check(session)
    session.git("add", "auth/session.py")
    session.git("commit", "-q", "-m", "agent commit")
    table = check(session)
    undo = undo_for(session, table, "auth/session.py")
    assert (undo.action, undo.reason) == ("refuse", REFUSE_COMMITTED)


def test_edited_again_after_a_commit_goes_back_to_the_commit(session):
    check(session)
    session.git("add", "auth/session.py")
    session.git("commit", "-q", "-m", "agent commit")
    check(session)
    session.write("auth/session.py", "TIMEOUT = 5\n")
    table = check(session)
    undo = undo_for(session, table, "auth/session.py")
    assert (undo.action, undo.before) == ("restore", b"TIMEOUT = 1440\n")


def test_big_file_is_refused(repo):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("data.csv", "a" * (BIG_FILE_BYTES + 1))
    snapshot.take(repo.root)
    repo.write("data.csv", "b" * (BIG_FILE_BYTES + 1))
    table = check(repo)
    undo = undo_for(repo, table, "data.csv")
    assert (undo.action, undo.reason) == ("refuse", REFUSE_BIG)


def test_copy_of_a_private_file_is_refused(session):
    session.write("backup.txt", "SECRET=changed\n")  # same bytes as the current .env
    table = check(session)
    undo = undo_for(session, table, "backup.txt")
    assert (undo.action, undo.reason) == ("refuse", REFUSE_COPY)


def test_file_put_back_by_hand_has_nothing_to_do(session):
    check(session)
    session.write("auth/session.py", "TIMEOUT = 30\n")
    table = check(session)
    undo = undo_for(session, table, "auth/session.py")
    assert undo.action == "nothing"


def test_unknown_id_lists_the_valid_ones(session):
    table = check(session)
    with pytest.raises(UnknownId) as error:
        plan_undo(session.root, table, 99)
    assert "no file with ID 99. Valid IDs: 1, 2, 3, 4, 5" in str(error.value)

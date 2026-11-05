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


# ---- step 2: back up, then write back or delete ----

from lineup.rollback import apply_undo  # noqa: E402


def roll_back(repo, table, path):
    return apply_undo(repo.root, undo_for(repo, table, path))


def test_files_go_back_byte_for_byte_with_a_backup(session):
    table = check(session)
    assert roll_back(session, table, "auth/session.py") == "restored"
    assert roll_back(session, table, "notes.py") == "restored"
    assert roll_back(session, table, "old.py") == "brought back"
    assert roll_back(session, table, "utils/cache.py") == "deleted"

    root = session.root
    assert (root / "auth/session.py").read_bytes() == b"TIMEOUT = 30\n"
    assert (root / "notes.py").read_bytes() == b"start\n"
    assert (root / "old.py").read_bytes() == b"OLD = 1\n"
    assert not (root / "utils").exists()  # the empty folder went too

    ids = {path: table["files"][path]["id"] for path in ("auth/session.py", "notes.py", "utils/cache.py")}
    assert store.read_backup(root, ids["auth/session.py"], "auth/session.py") == b"TIMEOUT = 1440\n"
    assert store.read_backup(root, ids["notes.py"], "notes.py") == b"start\nagent line\n"
    assert store.read_backup(root, ids["utils/cache.py"], "utils/cache.py") == b"def get(key):\n    return None\n"


def test_after_rollback_diff_shows_no_change_for_those_files(session):
    table = check(session)
    for path in ("auth/session.py", "notes.py", "old.py", "utils/cache.py"):
        roll_back(session, table, path)
    left = {c.path for c in find_changes(session.root, store.load_table(session.root))}
    assert left == {".env"}  # only the refused one is still changed


def test_deleted_file_comes_back_with_its_folder(repo):
    repo.write("lib/deep/util.py", "U = 1\n")
    repo.commit()
    snapshot.take(repo.root)
    repo.delete("lib/deep/util.py")
    (repo.root / "lib/deep").rmdir()
    (repo.root / "lib").rmdir()
    table = check(repo)
    assert roll_back(repo, table, "lib/deep/util.py") == "brought back"
    assert (repo.root / "lib/deep/util.py").read_bytes() == b"U = 1\n"


def test_refused_file_is_untouched_and_diary_has_names_only(session):
    table = check(session)
    assert roll_back(session, table, ".env") == "refused"
    assert (session.root / ".env").read_bytes() == b"SECRET=changed\n"
    roll_back(session, table, "auth/session.py")
    log = "\n".join(store.read_log(session.root))
    assert "rollback 1: .env refused" in log and "auth/session.py restored" in log
    assert "TIMEOUT" not in log and "SECRET" not in log


def test_committing_removes_the_backup(session):
    table = check(session)
    roll_back(session, table, "notes.py")
    file_id = table["files"]["notes.py"]["id"]
    assert store.read_backup(session.root, file_id, "notes.py") is not None
    session.write("notes.py", "start\nfinal\n")
    session.git("add", "notes.py")
    session.git("commit", "-q", "-m", "notes")
    check(session)
    assert store.read_backup(session.root, file_id, "notes.py") is None


# ---- step 3: the lineup rollback command ----

from lineup.cli import main  # noqa: E402


def run(repo, monkeypatch, capsys, *argv):
    monkeypatch.chdir(repo.root)
    capsys.readouterr()
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_command_rolls_back_one_or_many(session, monkeypatch, capsys):
    table = check(session)
    ids = {path: str(row["id"]) for path, row in table["files"].items() if "id" in row}
    code, out = run(session, monkeypatch, capsys, "rollback", ids["auth/session.py"])
    assert code == 0 and "auth/session.py  restored" in out
    code, out = run(session, monkeypatch, capsys, "rollback", ids["utils/cache.py"], ids["old.py"],
                    ids["utils/cache.py"])
    assert code == 0 and out.count("utils/cache.py") == 1  # repeated IDs count once
    assert "deleted" in out and "brought back" in out
    assert "saved in .git/lineup/backups/" in out
    _, out = run(session, monkeypatch, capsys, "diff")
    assert "auth/session.py" not in out and "utils/cache.py" not in out


def test_skipped_file_does_not_stop_the_others(session, monkeypatch, capsys):
    table = check(session)
    ids = {path: str(row["id"]) for path, row in table["files"].items() if "id" in row}
    code, out = run(session, monkeypatch, capsys, "rollback", ids[".env"], ids["notes.py"])
    assert code == 1
    assert ".env" in out and "skipped: private file (like .env) changed" in out
    assert "notes.py  restored" in out
    assert (session.root / "notes.py").read_bytes() == b"start\n"


def test_unknown_id_changes_nothing(session, monkeypatch, capsys):
    table = check(session)
    good = str(table["files"]["notes.py"]["id"])
    before = disk(session)
    code, out = run(session, monkeypatch, capsys, "rollback", good, "33")
    assert code == 1
    assert "No file with ID 33. Valid IDs: 1, 2, 3, 4, 5. Nothing was changed." in out
    assert disk(session) == before


def test_no_photo_refuses(repo, monkeypatch, capsys):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("app.py", "x = 2\n")
    code, out = run(repo, monkeypatch, capsys, "rollback", "1")
    assert code == 1 and "No snapshot from the session start" in out
    assert (repo.root / "app.py").read_bytes() == b"x = 2\n"


def test_file_committed_just_before_is_refused(session, monkeypatch, capsys):
    table = check(session)
    file_id = str(table["files"]["auth/session.py"]["id"])
    session.git("add", "auth/session.py")
    session.git("commit", "-q", "-m", "agent commit")  # no lineup check in between
    code, out = run(session, monkeypatch, capsys, "rollback", file_id)
    assert code == 1 and "committed, lineup can't undo it; use git to undo" in out
    assert (session.root / "auth/session.py").read_bytes() == b"TIMEOUT = 1440\n"


def test_no_ids_is_an_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["rollback"])
    assert exit_info.value.code == 2

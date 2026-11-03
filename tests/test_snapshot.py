import pytest

from lineup import changes, git, snapshot, store
from lineup.cli import main


@pytest.fixture
def project(repo):
    """Last commit has app code; notes.py is unfinished; .env is hidden by .gitignore."""
    repo.write(".gitignore", ".env\nnode_modules/\n")
    repo.write("auth/session.py", "TIMEOUT_MINUTES = 30\n")
    repo.write("notes.py", "todo = []\n")
    repo.write("old.py", "x = 1\n")
    repo.commit("start")
    repo.write("notes.py", "todo = ['finish login']\n")  # unfinished, not committed
    repo.write("draft.md", "# Draft\n")                  # new, not committed
    repo.write(".env", "SECRET_KEY=abc123\n")             # hidden by .gitignore
    repo.write("node_modules/lib/index.js", "x\n")       # hidden folder
    repo.delete("old.py")                                 # deleted before the session
    return repo


def test_photo_records_start_commit(project):
    table = snapshot.take(project.root)
    head = git.head_commit(project.root)
    assert table["start_commit"] == head
    assert table["last_seen_commit"] == head
    assert table["next_id"] == 1


def test_only_unfinished_files_are_copied(project):
    table = snapshot.take(project.root)
    files = table["files"]
    assert files["notes.py"] == {"at_start": "uncommitted", "copy": True}
    assert files["draft.md"] == {"at_start": "uncommitted", "copy": True}
    assert "auth/session.py" not in files  # committed: the commit ID is enough
    assert store.read_copy(project.root, "notes.py") == b"todo = ['finish login']\n"
    assert store.read_copy(project.root, "auth/session.py") is None


def test_hidden_env_gets_fingerprint_only(project):
    table = snapshot.take(project.root)
    env = table["files"][".env"]
    assert env["at_start"] == "private"
    assert env["hash"] == changes.fingerprint(project.root / ".env")
    assert len(env["hash"]) == 64
    assert store.read_copy(project.root, ".env") is None
    for file in store.folder(project.root).rglob("*"):
        if file.is_file():
            assert b"abc123" not in file.read_bytes(), file


def test_hidden_folders_are_skipped(project):
    files = snapshot.take(project.root)["files"]
    assert not any(path.startswith("node_modules") for path in files)


def test_file_deleted_before_session(project):
    assert snapshot.take(project.root)["files"]["old.py"] == {"at_start": "missing"}


def test_big_file_not_copied(project, monkeypatch):
    monkeypatch.setattr(changes, "BIG_FILE_BYTES", 40)  # notes.py is 24 bytes, model.bin 50
    project.write("model.bin", "x" * 50)
    table = snapshot.take(project.root)
    assert table["files"]["model.bin"]["at_start"] == "big"
    assert table["files"]["model.bin"]["size"] == 50
    assert store.read_copy(project.root, "model.bin") is None
    assert "1 big file can't be undone" in snapshot.summary(table)


def test_keep_leaves_existing_photo(project):
    first = snapshot.take(project.root)
    project.write("notes.py", "changed by claude\n")
    kept = snapshot.take(project.root, keep=True)
    assert kept == first
    assert store.read_copy(project.root, "notes.py") == b"todo = ['finish login']\n"
    assert store.read_log(project.root)[-1].endswith(f"photo kept (taken {first['taken_at']} at {first['start_commit'][:7]})")


def test_fresh_photo_replaces_old_copies(project):
    snapshot.take(project.root)
    project.git("add", "-A")
    project.git("commit", "-q", "-m", "everything")
    table = snapshot.take(project.root)
    assert table["files"] == {".env": table["files"][".env"]}
    assert not (store.folder(project.root) / "copies").exists()


def test_keep_without_photo_takes_one(project):
    assert snapshot.take(project.root, keep=True)["files"]["notes.py"]["copy"] is True


def test_empty_repo(repo):
    repo.write("app.py", "x = 1\n")
    table = snapshot.take(repo.root)
    assert table["start_commit"] == git.EMPTY_TREE
    assert table["files"]["app.py"]["copy"] is True


def test_diary_line(project):
    snapshot.take(project.root)
    line = store.read_log(project.root)[-1]
    assert "snapshot at" in line and "2 copies, 1 fingerprint" in line


def test_hook_start_command(project, monkeypatch, capsys):
    monkeypatch.chdir(project.root)
    assert main(["hook", "start"]) == 0
    out = capsys.readouterr().out
    head = git.head_commit(project.root)[:7]
    assert out.strip() == f"lineup: photo taken at {head} (2 copies, 1 fingerprint)"

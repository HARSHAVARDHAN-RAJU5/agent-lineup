import re

import pytest

from lineup import store


def test_drawer_lives_inside_git(repo):
    drawer = store.folder(repo.root)
    assert drawer.resolve() == (repo.root / ".git" / "lineup").resolve()
    assert drawer.is_dir()


def test_no_photo_yet(repo):
    assert not store.has_snapshot(repo.root)
    assert store.load_table(repo.root) == {}


def test_table_round_trip(repo):
    table = {"start_commit": "83f6195", "files": {"notes.py": {"at_start": "uncommitted", "copy": True}}}
    store.save_table(repo.root, table)
    assert store.has_snapshot(repo.root)
    assert store.load_table(repo.root) == table
    assert not (store.folder(repo.root) / "table.json.tmp").exists()


def test_copy_is_exact_bytes(repo):
    data = b"line one\r\nline two\n\x00binary"
    store.save_copy(repo.root, "notes.py", data)
    assert store.read_copy(repo.root, "notes.py") == data


def test_copies_keep_their_folders(repo):
    store.save_copy(repo.root, "src/notes.py", b"src")
    store.save_copy(repo.root, "docs/notes.py", b"docs")
    assert store.read_copy(repo.root, "src/notes.py") == b"src"
    assert store.read_copy(repo.root, "docs/notes.py") == b"docs"


def test_delete_copy_cleans_empty_folders(repo):
    store.save_copy(repo.root, "src/app/notes.py", b"x")
    store.delete_copy(repo.root, "src/app/notes.py")
    assert store.read_copy(repo.root, "src/app/notes.py") is None
    assert not (store.folder(repo.root) / "copies" / "src").exists()
    store.delete_copy(repo.root, "never/saved.py")  # no error


@pytest.mark.parametrize("path", ["../outside.py", "/etc/passwd", "a/../../b.py", ""])
def test_unsafe_paths_refused(repo, path):
    with pytest.raises(ValueError):
        store.save_copy(repo.root, path, b"x")


def test_diary_only_grows(repo):
    store.log(repo.root, "snapshot at 83f6195: 1 copy, 1 fingerprint")
    store.log(repo.root, "checked: 4 files changed")
    lines = store.read_log(repo.root)
    assert len(lines) == 2
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} snapshot at 83f6195", lines[0])
    assert lines[1].endswith("checked: 4 files changed")


def test_diary_squashes_multiline_messages(repo):
    store.log(repo.root, "flagged:\ndef secret():\n    return 1")
    lines = store.read_log(repo.root)
    assert len(lines) == 1
    assert lines[0].endswith("flagged: def secret(): return 1")

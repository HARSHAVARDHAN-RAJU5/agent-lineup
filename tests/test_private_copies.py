"""Fix 3: a file renamed or copied from a private file stays private."""

from lineup import rules, snapshot, store
from lineup.changes import find_changes
from lineup.guard import ai_input, build_message


def project(repo):
    repo.write(".gitignore", ".env\n")
    repo.write("app.py", "x = 1\n")
    repo.write(".env", "SECRET_KEY=S3CR3T_555\n")
    repo.commit()
    snapshot.take(repo.root)
    return repo


def changed(repo):
    return {c.path: c for c in find_changes(repo.root, store.load_table(repo.root))}


def test_env_renamed_and_copied_stays_private(repo):
    project(repo)
    (repo.root / ".env").rename(repo.root / "settings.txt")
    repo.write("backup.txt", "SECRET_KEY=S3CR3T_555\n")
    found = changed(repo)
    for name in ("settings.txt", "backup.txt"):
        assert found[name].private and found[name].copy_of_private and found[name].diff == ""
        flags = rules.rules_for(found[name], list(found.values()))
        assert ("HIGH", "copy of a private file, contents not read") in flags
    message = build_message(ai_input(list(found.values()), {})[0])
    assert "S3CR3T_555" not in message
    assert "--- backup.txt (LOW): copy of a private file, contents not read" in message


def test_copy_while_env_still_exists(repo):
    project(repo)
    repo.write("backup.txt", "SECRET_KEY=S3CR3T_555\n")
    assert changed(repo)["backup.txt"].copy_of_private


def test_fresh_photo_keeps_only_a_fingerprint_of_the_copy(repo):
    project(repo)
    (repo.root / ".env").rename(repo.root / "settings.txt")
    table = snapshot.take(repo.root)  # .env is gone now; its old fingerprint still marks the copy
    assert table["files"]["settings.txt"]["at_start"] == "private"
    assert store.read_copy(repo.root, "settings.txt") is None
    assert "S3CR3T_555" not in (repo.root / ".git/lineup/table.json").read_text(encoding="utf-8")


def test_normal_and_empty_files_are_not_affected(repo):
    project(repo)
    repo.write("notes.txt", "hello\n")
    repo.write("empty.txt", "")
    found = changed(repo)
    assert not found["notes.txt"].private
    assert not found["empty.txt"].private

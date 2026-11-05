"""Phase 6 fixes: links and paths outside the repo, clean errors, backups, staged files,
line endings, files changed since the last check, and the executable permission."""

import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

from lineup import changes, rollback, snapshot, store
from lineup.changes import find_changes
from lineup.cli import main
from lineup.guard import ai_input, build_message


def run(repo, monkeypatch, capsys, *argv):
    monkeypatch.chdir(repo.root)
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def ids(repo, monkeypatch, capsys):
    run(repo, monkeypatch, capsys, "risky")
    table = store.load_table(repo.root)
    return {p: str(r["id"]) for p, r in table["files"].items() if "id" in r}


def start(repo, monkeypatch, capsys):
    run(repo, monkeypatch, capsys, "hook", "start")


# ---- 1. links and paths outside the repo ----

def _make_junction(link, target):
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return result.returncode == 0


def _make_dir_symlink(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def _folder_link(kind, link, target):
    if kind == "junction":
        if os.name != "nt" or not _make_junction(link, target):
            pytest.skip("Windows junctions only")
    elif not _make_dir_symlink(link, target):
        pytest.skip("this machine can't create symlinks (runs on Mac/Linux)")


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_folder_replaced_by_a_link_is_never_read_copied_or_written(repo, tmp_path_factory, monkeypatch, capsys, kind):
    outside = tmp_path_factory.mktemp("outside")
    repo.write("lib/x.py", "X = 1\n")
    repo.write("app.py", "a = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    shutil.rmtree(repo.root / "lib")
    (outside / "x.py").write_text("SECRET_OUTSIDE = 1\n")
    _folder_link(kind, repo.root / "lib", outside)

    found = find_changes(repo.root, store.load_table(repo.root))
    x = next(c for c in found if c.path == "lib/x.py")
    assert x.symlink and x.diff == ""  # name only
    message = build_message(ai_input(found, {})[0])
    assert "SECRET_OUTSIDE" not in message and "lib/x.py (LOW): link, not followed" in message

    table = snapshot.take(repo.root)  # a fresh photo copies nothing behind the link
    assert store.read_copy(repo.root, "lib/x.py") is None
    assert "SECRET_OUTSIDE" not in json.dumps(table)

    snapshot.take(repo.root)
    repo_ids = ids(repo, monkeypatch, capsys)
    before = (outside / "x.py").read_bytes()
    code, out = run(repo, monkeypatch, capsys, "rollback", repo_ids["lib/x.py"])
    assert code == 1 and "inside a linked folder (lib), not followed" in out
    assert (outside / "x.py").read_bytes() == before and sorted(p.name for p in outside.iterdir()) == ["x.py"]


def test_table_entry_outside_the_repo_is_refused_cleanly(repo, tmp_path_factory, monkeypatch, capsys):
    repo.write("a.py", "a = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    precious = repo.root.parent / "outside.py"
    precious.write_text("precious\n")
    table = store.load_table(repo.root)
    table["files"]["../outside.py"] = {"at_start": "missing", "id": 50, "status": "changed"}
    store.save_table(repo.root, table)
    code, out = run(repo, monkeypatch, capsys, "rollback", "50")
    assert code == 1 and "outside the repo, lineup never touches it" in out
    assert "Traceback" not in out and precious.read_text() == "precious\n"
    assert find_changes(repo.root, store.load_table(repo.root)) == []  # never read either


def test_paths_check(repo):
    from lineup import paths
    repo.write("a.py", "x\n")
    assert paths.unsafe(repo.root, "a.py") is None
    assert paths.unsafe(repo.root, "../a.py") == paths.OUTSIDE
    assert paths.unsafe(repo.root, "/etc/passwd") == paths.OUTSIDE


# ---- 2. clean errors ----

def test_file_replaced_by_a_folder_is_refused_without_changes(repo, monkeypatch, capsys):
    repo.write("config", "SETTING = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    (repo.root / "config").unlink()
    repo.write("config/settings.py", "S = 1\n")
    repo_ids = ids(repo, monkeypatch, capsys)
    code, out = run(repo, monkeypatch, capsys, "rollback", repo_ids["config"])
    assert code == 1 and "a folder now has this name, so nothing was changed" in out
    assert "Traceback" not in out and (repo.root / "config/settings.py").read_text() == "S = 1\n"


def test_folder_replaced_by_a_file_is_refused_without_changes(repo, monkeypatch, capsys):
    repo.write("lib/u.py", "U = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    shutil.rmtree(repo.root / "lib")
    repo.write("lib", "now a file\n")
    repo_ids = ids(repo, monkeypatch, capsys)
    code, out = run(repo, monkeypatch, capsys, "rollback", repo_ids["lib/u.py"])
    assert code == 1 and "a file now has the name of its folder (lib)" in out
    assert (repo.root / "lib").read_text() == "now a file\n"


# ---- 3. backups ----

def test_every_rollback_keeps_its_own_backup_folder(repo, monkeypatch, capsys):
    repo.write("a.py", "a = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    repo.write("a.py", "v1\n")
    file_id = ids(repo, monkeypatch, capsys)["a.py"]
    _, out1 = run(repo, monkeypatch, capsys, "rollback", file_id)
    repo.write("a.py", "v2\n")
    ids(repo, monkeypatch, capsys)
    _, out2 = run(repo, monkeypatch, capsys, "rollback", file_id)
    runs = store.backup_runs(repo.root)
    assert len(runs) == 2
    assert [store.read_backup(repo.root, r, "a.py") for r in runs] == [b"v1\n", b"v2\n"]
    assert f".git/lineup/backups/{runs[0]}/" in out1 and f".git/lineup/backups/{runs[1]}/" in out2


# ---- 4. staged files ----

def test_staged_copy_is_left_alone_and_warned_about(repo, monkeypatch, capsys):
    repo.write("a.py", "a = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    repo.write("a.py", "a = CLAUDE\n")
    repo.git("add", "a.py")
    file_id = ids(repo, monkeypatch, capsys)["a.py"]
    _, out = run(repo, monkeypatch, capsys, "rollback", file_id)
    assert "The staged copy of a.py still has the old change. Run git restore --staged a.py before committing." in out
    assert "+a = CLAUDE" in repo.git("diff", "--cached")  # git's staged copy untouched
    _, risky = run(repo, monkeypatch, capsys, "risky")
    assert "no changes since the photo" not in risky
    assert "but a staged copy still has a change" in risky and "git restore --staged a.py" in risky


# ---- 5. line endings ----

def test_restore_matches_checkout_with_autocrlf(repo, monkeypatch, capsys):
    repo.git("config", "core.autocrlf", "true")
    repo.write("win.txt", "a\r\nb\r\n")
    repo.commit()
    on_disk = (repo.root / "win.txt").read_bytes()
    start(repo, monkeypatch, capsys)
    _, out = run(repo, monkeypatch, capsys, "risky")
    assert "no changes since the photo" in out  # no false change from line endings
    repo.write("win.txt", "a\r\nCLAUDE\r\n")
    run(repo, monkeypatch, capsys, "rollback", ids(repo, monkeypatch, capsys)["win.txt"])
    assert (repo.root / "win.txt").read_bytes() == on_disk == b"a\r\nb\r\n"


def test_restore_respects_gitattributes(repo, monkeypatch, capsys):
    repo.write(".gitattributes", "*.bat text eol=crlf\n")
    repo.write("run.bat", "echo a\r\necho b\r\n")
    repo.commit()
    (repo.root / "run.bat").unlink()
    repo.git("checkout", "--", "run.bat")  # what git itself writes
    expected = (repo.root / "run.bat").read_bytes()
    start(repo, monkeypatch, capsys)
    repo.write("run.bat", "echo CLAUDE\n")
    run(repo, monkeypatch, capsys, "rollback", ids(repo, monkeypatch, capsys)["run.bat"])
    assert (repo.root / "run.bat").read_bytes() == expected


# ---- 6. changed since the last check ----

def test_warns_when_the_file_changed_since_the_last_check(repo, monkeypatch, capsys):
    repo.write("a.py", "a = 1\nb = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    repo.write("a.py", "a = 1\nb = 1\nclaude\n")
    a_id = ids(repo, monkeypatch, capsys)["a.py"]
    repo.write("a.py", "a = 1\nb = 1\nclaude\nMY EDIT\n")  # after lineup last looked
    code, out = run(repo, monkeypatch, capsys, "rollback", a_id)
    assert rollback.WARN_CHANGED in out and code == 0  # warned, not blocked
    assert (repo.root / "a.py").read_bytes() == b"a = 1\nb = 1\n"
    run_folder = store.backup_runs(repo.root)[-1]
    assert store.read_backup(repo.root, run_folder, "a.py") == b"a = 1\nb = 1\nclaude\nMY EDIT\n"


def test_no_warning_when_nothing_changed_since_the_check(repo, monkeypatch, capsys):
    repo.write("a.py", "a = 1\n")
    repo.commit()
    start(repo, monkeypatch, capsys)
    repo.write("a.py", "claude\n")
    _, out = run(repo, monkeypatch, capsys, "rollback", ids(repo, monkeypatch, capsys)["a.py"])
    assert "Warning" not in out


# ---- 7. executable permission ----

def test_executable_bit_comes_back_from_git(repo, monkeypatch, capsys):
    repo.write("run.sh", "echo hi\n")
    repo.git("add", "run.sh")
    repo.git("update-index", "--chmod=+x", "run.sh")
    repo.git("commit", "-q", "-m", "script")
    start(repo, monkeypatch, capsys)
    (repo.root / "run.sh").unlink()
    file_id = ids(repo, monkeypatch, capsys)["run.sh"]
    calls = []
    real_chmod = os.chmod
    monkeypatch.setattr(rollback.os, "chmod", lambda p, m: (calls.append((os.path.basename(p), m)), real_chmod(p, m)))
    run(repo, monkeypatch, capsys, "rollback", file_id)
    assert calls and calls[0][0] == "run.sh" and calls[0][1] & 0o111 == 0o111
    if os.name != "nt":  # Windows has no executable bit to check
        assert os.access(repo.root / "run.sh", os.X_OK)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no executable bit")
def test_executable_bit_comes_back_from_the_saved_copy(repo, monkeypatch, capsys):
    repo.write("a.py", "x\n")
    repo.commit()
    repo.write("tool.sh", "echo hi\n")
    os.chmod(repo.root / "tool.sh", 0o755)
    start(repo, monkeypatch, capsys)  # uncommitted: saved copy + mode
    (repo.root / "tool.sh").unlink()
    run(repo, monkeypatch, capsys, "rollback", ids(repo, monkeypatch, capsys)["tool.sh"])
    assert os.access(repo.root / "tool.sh", os.X_OK)

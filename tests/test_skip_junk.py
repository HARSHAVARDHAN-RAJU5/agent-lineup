"""Caches, build output and git-ignored files are skipped: not scored, shown, copied, sent or suggested."""

import io
import json
import subprocess

import pytest

from lineup import changes, git, store
from lineup.cli import main

PYC = "__pycache__/auth.cpython-310.pyc"


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def stop(repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo.root)})))
    _, out = run(capsys, "hook", "stop", "--agent", "claude")
    return json.loads(out).get("systemMessage", "")


@pytest.fixture
def project(repo, monkeypatch):
    """A repo with no .gitignore: the built-in skip list must still apply."""
    repo.write("auth.py", "TIMEOUT = 30\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    return repo


def test_pyc_named_auth_is_not_flagged_and_undo_points_to_the_real_file(project, monkeypatch, capsys):
    project.write("auth.py", "TIMEOUT = 1440\n")
    (project.root / "__pycache__").mkdir()
    (project.root / PYC).write_bytes(b"\x00compiled auth")
    message = stop(project, monkeypatch, capsys)
    file_id = store.load_table(project.root)["files"]["auth.py"]["id"]
    assert "pyc" not in message and "__pycache__" not in message
    assert message.splitlines()[-1] == f"Undo: lineup rollback {file_id}"
    assert "1 HIGH" in message
    _, out = run(capsys, "risky")
    assert "pyc" not in out and "auth.py" in out


def test_repo_without_gitignore_skips_every_built_in_kind(project):
    for path in (PYC, "lib/x.pyo", "node_modules/pkg/index.js", ".venv/lib/site.py", "venv/bin/activate",
                 ".pytest_cache/v/cache/lastfailed", ".mypy_cache/3.10/auth.json", ".DS_Store", "sub/.DS_Store"):
        project.write(path, "junk\n")
    project.write("real.py", "x = 1\n")
    found = [c.path for c in changes.find_changes(project.root, store.load_table(project.root))]
    assert found == ["real.py"]


def test_gitignored_file_is_not_shown_even_if_tracked(repo, monkeypatch, capsys):
    repo.write("build/out.js", "v1\n")
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write(".gitignore", "build/\n")  # ignored after it was committed: git still tracks it
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    repo.write("build/out.js", "v2 with auth token handling\n")
    repo.write("app.py", "x = 2\n")
    _, out = run(capsys, "risky")
    assert "build/out.js" not in out and "app.py" in out
    _, out = run(capsys, "diff")
    assert "build/out.js" not in out


def test_skipped_files_are_not_copied_into_the_photo(repo, monkeypatch):
    repo.write("a.py", "a = 1\n")
    repo.commit()
    repo.write(PYC, "compiled\n")
    repo.write("draft.py", "mine\n")
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    files = store.load_table(repo.root)["files"]
    assert "draft.py" in files and PYC not in files
    assert store.read_copy(repo.root, PYC) is None


def test_skipped_files_never_reach_the_committed_group(project, capsys):
    project.write(PYC, "compiled auth\n")
    project.write("auth.py", "TIMEOUT = 1\n")
    project.git("add", "-A")
    project.git("commit", "-q", "-m", "with junk")
    _, out = run(capsys, "risky")
    committed = out.split("Committed")[1] if "Committed" in out else ""
    assert "auth.py" in committed and "pyc" not in committed


def test_private_files_are_still_tracked_when_git_ignores_them(repo, monkeypatch, capsys):
    repo.write(".gitignore", ".env\n")
    repo.write("a.py", "a = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    repo.write(".env", "TOKEN=abc\n")
    _, out = run(capsys, "risky")
    assert ".env" in out and "HIGH" in out


def test_rollback_by_name_of_a_skipped_file_is_clear(project, capsys):
    project.write("auth.py", "TIMEOUT = 1440\n")
    project.write(PYC, "compiled\n")
    run(capsys, "risky")
    code, out = run(capsys, "rollback", "auth.cpython-310.pyc")
    assert code == 1 and "Traceback" not in out
    assert "is a cache file or a file git ignores" in out and "Nothing was changed." in out
    assert (project.root / "auth.py").read_text() == "TIMEOUT = 1440\n"


def test_old_photo_rows_for_junk_cant_be_rolled_back(project, capsys):
    """A 0.1.0 photo may already hold a __pycache__ row with an ID."""
    project.write(PYC, "compiled\n")
    table = store.load_table(project.root)
    table["files"][PYC] = {"at_start": "missing", "id": 9, "status": "changed"}
    store.save_table(project.root, table)
    code, out = run(capsys, "rollback", "9")
    assert code == 1 and "No file with ID 9." in out and (project.root / PYC).exists()


def test_many_untracked_files_use_one_batched_git_call(repo, monkeypatch):
    repo.write(".gitignore", "logs/\n")
    repo.write("a.py", "a = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    for n in range(300):
        repo.write(f"gen/file{n}.py", f"x = {n}\n")
        repo.write(f"__pycache__/file{n}.cpython-310.pyc", "c\n")
    calls = []
    real_run = subprocess.run

    def counting(args, *a, **k):
        if list(args[:2]) == ["git", "check-ignore"]:
            calls.append(args)
        return real_run(args, *a, **k)

    monkeypatch.setattr(git.subprocess, "run", counting)
    found = changes.find_changes(repo.root, store.load_table(repo.root))
    assert len(found) == 300 and not any("pycache" in c.path for c in found)
    assert len(calls) == 1  # one call for all 300 files, never one per file

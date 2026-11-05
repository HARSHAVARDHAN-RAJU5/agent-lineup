"""lineup report: counts and dates only, from local data. No code, file names or secrets."""

import io
import json
import re

from lineup import store
from lineup.cli import main

SECRET = "sk-live-ABCDEF1234567890abcdef"


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def hook(monkeypatch, repo, event):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo.root), "source": "startup"})))
    main(["hook", event, "--agent", "claude"])


def test_no_usage_yet(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    code, out = run(capsys, "report")
    assert code == 0 and out.strip() == "lineup report: no usage recorded in this repo yet."


def test_report_counts_a_session_and_holds_no_code_names_or_secrets(repo, monkeypatch, capsys):
    repo.write("auth.py", "TIMEOUT = 30\n")
    repo.write("billing_secret_module.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    hook(monkeypatch, repo, "start")
    repo.write("auth.py", f"TIMEOUT = 1440\nAPI_KEY = '{SECRET}'\n")
    repo.write("notes.py", "def helper():\n    return 42\n")
    hook(monkeypatch, repo, "stop")
    run(capsys, "risky")
    run(capsys, "diff")
    run(capsys, "rollback", "notes.py")
    run(capsys, "rollback", "auth.py")
    code, out = run(capsys, "report")
    assert code == 0
    assert "Sessions started" in out and re.search(r"Sessions started\s+1\n", out)
    assert re.search(r"Agent replies with changes\s+1\n", out)
    assert "HIGH 1, MED 0, LOW 1" in out
    assert re.search(r"lineup diff runs\s+1\n", out) and re.search(r"lineup risky runs\s+1\n", out)
    assert "Rollbacks" in out and "done 2, refused 0, failed 0" in out
    assert re.search(r"Days active\s+1 \(\d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d\)", out)
    assert "Nothing has been sent" in out
    # nothing from the repo: no file names, code or secrets
    for leak in ("auth.py", "notes.py", "billing", "TIMEOUT", "1440", "helper", "API_KEY", SECRET, str(repo.root)):
        assert leak not in out, leak
    # the saved data holds numbers and dates only
    saved = (repo.root / ".git/lineup" / store.USAGE).read_text(encoding="utf-8")
    assert all(leak not in saved for leak in ("auth.py", "notes.py", "TIMEOUT", SECRET))


def test_report_ignores_unknown_or_bad_entries(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    store.folder(repo.root)
    (repo.root / ".git/lineup" / store.USAGE).write_text(
        json.dumps({"counts": {"sessions": 2, "auth.py": 5, "risky_runs": "auth.py"}, "days": ["2026-11-10", 7]}),
        encoding="utf-8")
    _, out = run(capsys, "report")
    assert "auth.py" not in out and re.search(r"Sessions started\s+2\n", out)


def test_broken_usage_file_never_breaks_a_command(repo, monkeypatch, capsys):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    store.folder(repo.root)
    (repo.root / ".git/lineup" / store.USAGE).write_text("not json", encoding="utf-8")
    code, _ = run(capsys, "risky")
    assert code == 0
    _, out = run(capsys, "report")
    assert re.search(r"lineup risky runs\s+1\n", out)

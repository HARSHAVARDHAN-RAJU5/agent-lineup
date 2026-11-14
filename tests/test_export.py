"""lineup report --export: lineup-notes.md holds no file names, paths, code or secrets, and lineup skips it."""

import io
import json
import re
import sys

import pytest

from lineup import changes, llm, store
from lineup.cli import main

NAMES = ("zebra_invoice_xyz", "quokka_ledger", "secret_billing_xyz", "tests/test_quokka", "src/quokka")
CODE = "UNIQUE_CODE_STRING_7f3a9"
KEYS = ("sk-live-EXPORTKEY1234567890abcd", "AKIAEXPORTKEY1234567", "ghp_ExportKey1234567890abcdefghijklmnop")
NOTE_KEY = "sk_live_NOTEEXPORT1234567890"

FAKE_AGENT = r'''
import json, re, sys
paths = re.findall(r"^--- (\S+) \(", sys.stdin.read(), re.M)
print(json.dumps({"result": json.dumps({"overview": "ok", "files": {p: "changed " + p for p in paths}}),
                  "usage": {"input_tokens": 50, "output_tokens": 7}}))
'''


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def hook(monkeypatch, repo, event, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo.root), "source": "startup"})))
    capsys.readouterr()
    main(["hook", event, "--agent", "claude"])
    capsys.readouterr()


@pytest.fixture
def session(repo, monkeypatch, capsys, tmp_path_factory):
    """A session's worth of logging in a repo full of distinctive names, code and fake keys."""
    script = tmp_path_factory.mktemp("fake") / "agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    monkeypatch.setitem(llm.AGENTS, "claude", ["fake-claude", str(script)])
    monkeypatch.setattr(llm, "installed", lambda c: sys.executable if c == "fake-claude" else None)

    repo.write("src/quokka/zebra_invoice_xyz.py", "RATE = 1\n")
    repo.write("src/quokka/quokka_ledger.py", "def total():\n    return 1\n")
    repo.write("tests/test_quokka.py", "def test_total():\n    assert total() == 1\n")
    repo.write("secret_billing_xyz.py", "X = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    run(capsys, "init", "--yes", "--agent", "claude")
    hook(monkeypatch, repo, "start", capsys)
    repo.write("src/quokka/zebra_invoice_xyz.py", f"RATE = 2\n{CODE} = '{KEYS[0]}'\n")
    repo.write("src/quokka/quokka_ledger.py", f"def total():\n    return 2  # {KEYS[1]}\n")
    repo.write("tests/test_quokka.py", "def test_total():\n    pass\n")
    repo.write("secret_billing_xyz.py", f"TOKEN = '{KEYS[2]}'\n")
    repo.write(".env", f"API_KEY={KEYS[0]}\n")
    hook(monkeypatch, repo, "stop", capsys)
    run(capsys, "risky")
    run(capsys, "diff")
    run(capsys, "rollback", "zebra_invoice_xyz.py")
    hook(monkeypatch, repo, "prompt", capsys)
    run(capsys, "note", f"the HIGH flag was right, the agent leaked {NOTE_KEY}")
    run(capsys, "note", "rollback felt slow")
    run(capsys, "report")
    return repo


def test_export_holds_no_names_paths_code_or_secrets(session, capsys):
    code, out = run(capsys, "report", "--export")
    assert code == 0
    text = (session.root / "lineup-notes.md").read_text(encoding="utf-8")
    for leak in (*NAMES, CODE, *KEYS, NOTE_KEY, "RATE", "total()", ".env", ".py", str(session.root),
                 session.root.name, "API_KEY", "TOKEN"):
        assert leak not in text, leak
    assert not re.search(r"[A-Za-z0-9_]+\.(py|js|ts|json|md)\b", text.replace("lineup-notes.md", ""))
    # and it has what it should
    assert text.startswith("# lineup notes")
    assert ("Read this before sending. It should contain no file names, code or secrets. "
            "If you see any, delete them.") in text.split("## Summary")[0]
    assert "| Sessions started | 1 |" in text and "| Agents | claude |" in text
    assert "| assertion_removed | HIGH | test | 1 |" in text
    assert "| private_file | HIGH | env | 2 |" in text  # .env and secret_billing_xyz.py (a private name)
    assert re.search(r"\| python \| done \| \d+s \|", text)  # a rollback, by file type, with time after the flag
    assert re.search(r"\| claude \| [\d.]+ \| 57 \| \d+ \| ok \|", text)  # AI time and tokens
    assert "the HIGH flag was right, the agent leaked [REDACTED]" in text and "rollback felt slow" in text
    assert "| init |" in text and "| session start | claude |" in text


def test_export_never_uses_the_audit_log(session, capsys):
    diary = "\n".join(store.read_log(session.root))
    assert "zebra_invoice_xyz" in diary  # the audit log does hold names...
    run(capsys, "report", "--export")
    assert "zebra_invoice_xyz" not in (session.root / "lineup-notes.md").read_text(encoding="utf-8")  # ...never exported


def test_export_message_and_exclude(session, capsys):
    code, out = run(capsys, "report", "--export")
    assert code == 0 and f"Wrote {session.root / 'lineup-notes.md'}" in out
    assert "Read this before sending" in out and "Nothing has been sent" in out
    assert "/lineup-notes.md" in (session.root / ".git/info/exclude").read_text(encoding="utf-8").splitlines()
    assert session.git("status", "--porcelain", "--", "lineup-notes.md") == ""


def test_export_file_is_never_flagged_or_snapshotted(repo, monkeypatch, capsys):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    run(capsys, "report", "--export")
    (repo.root / ".git/info/exclude").write_text("", encoding="utf-8")  # even if it isn't excluded any more
    hook(monkeypatch, repo, "start", capsys)
    assert "lineup-notes.md" not in store.load_table(repo.root)["files"]
    run(capsys, "report", "--export")  # rewritten during the session
    found = changes.find_changes(repo.root, store.load_table(repo.root))
    assert "lineup-notes.md" not in [c.path for c in found]
    _, out = run(capsys, "risky")
    assert "lineup-notes.md" not in out


def test_export_with_no_usage_yet(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    code, _ = run(capsys, "report", "--export")
    text = (repo.root / "lineup-notes.md").read_text(encoding="utf-8")
    assert code == 0 and "## Your notes" in text and "_No notes." in text

"""Phase 4 step 4: the AI Summary in `lineup diff` (fake agent, no real AI)."""

import json
import sys

import pytest

from lineup import llm
from lineup.cli import main

FAKE_AGENT = r'''
import json, re, sys
message = sys.stdin.read()
paths = [p for p in re.findall(r"^--- (\S+) \(", message, re.M) if p != ".env"]
files = {p: "AI line for " + p for p in paths}
print(json.dumps({"result": json.dumps({"overview": "Claude raised the timeout.", "files": files})}))
'''


@pytest.fixture
def project(repo, monkeypatch):
    repo.write(".gitignore", ".env\n")
    repo.write("auth/session.py", "TIMEOUT = 30\n")
    repo.write("notes.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start", "--agent", "claude"])
    repo.write("auth/session.py", "TIMEOUT = 1440\n")
    repo.write("notes.py", "x = 2\n")
    repo.write(".env", "SECRET=abc123\n")
    return repo


@pytest.fixture
def fake_claude(tmp_path_factory, monkeypatch):
    script = tmp_path_factory.mktemp("fake") / "fake_claude.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    monkeypatch.setitem(llm.AGENTS, "claude", ["fake-claude", str(script)])
    monkeypatch.setattr(llm, "installed", lambda command: sys.executable if command == "fake-claude" else None)


def diff(capsys):
    capsys.readouterr()
    main(["diff"])
    return capsys.readouterr()


def test_summary_on_top_then_groups(project, fake_claude, capsys):
    out = diff(capsys)
    text = out.out
    assert "Summary (AI guess, by claude)\n  Claude raised the timeout." in text
    assert text.index("Summary") < text.index("What changed (AI)") < text.index("Additions")
    rows = [line for line in text.splitlines() if "AI line for" in line or "contents not read" in line]
    assert ".env" in rows[0] and "Private, contents not read" in rows[0]  # HIGH first, then by name
    assert "auth/session.py" in rows[1] and "HIGH" in rows[1]
    assert "notes.py" in rows[2] and "LOW" in rows[2]
    assert "Asking claude for a summary..." in out.err
    assert "abc123" not in text


def test_second_diff_asks_nobody(project, fake_claude, capsys):
    diff(capsys)
    out = diff(capsys)
    assert "Asking" not in out.err
    assert "Claude raised the timeout." in out.out


def test_no_agent_still_shows_the_groups(project, capsys):
    text = diff(capsys).out
    assert "Summary unavailable: claude not found" in text  # the session's agent isn't installed
    assert "Additions" in text and "notes.py" in text

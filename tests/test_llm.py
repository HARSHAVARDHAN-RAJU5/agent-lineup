"""Phase 4 step 2: the AI runner, tested with a fake agent (no real AI is called)."""

import json
import sys

import pytest

from lineup import llm, store
from lineup.cli import main

FAKE_AGENT = r'''
import json, os, sys
message = sys.stdin.read()
mode = os.environ.get("FAKE_MODE", "ok")
seen = {"empty_folder": os.listdir(".") == [], "loop_guard": os.environ.get("LINEUP_RUNNING"),
        "got_message": "<data>" in message}
if mode == "fail":
    sys.exit(1)
if mode == "slow":
    import time; time.sleep(10)
if mode == "garbage":
    print(json.dumps({"result": "sorry, I can't do that"}))
    sys.exit(0)
summary = {"overview": "Raised the timeout. " + json.dumps(seen),
           "files": {"auth.py": "Timeout raised", "made_up.py": "invented by the AI", "notes.py": "x" * 500}}
text = json.dumps(summary)
if mode == "fences":
    text = "```json\n" + text + "\n```"
if mode == "codex":
    print(json.dumps({"type": "thread.started"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
else:
    print(json.dumps({"type": "result", "result": text, "usage": {"input_tokens": 10}}))
'''


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    return [sys.executable, str(script)]


def ask(fake, monkeypatch, mode="ok", agent="claude", **extra):
    monkeypatch.setenv("FAKE_MODE", mode)
    return llm.run("rules\n<data>\n--- auth.py (HIGH)\n+x\n</data>\n", agent, ["auth.py", "notes.py"],
                   command=fake, **extra)


def test_runs_in_an_empty_folder_with_the_loop_guard_and_stdin(fake, monkeypatch):
    summary, reason = ask(fake, monkeypatch)
    assert reason is None
    assert '"empty_folder": true' in summary["overview"]
    assert '"loop_guard": "1"' in summary["overview"]
    assert '"got_message": true' in summary["overview"]


def test_invented_files_are_dropped_and_long_lines_cut(fake, monkeypatch):
    summary, _ = ask(fake, monkeypatch)
    assert set(summary["files"]) == {"auth.py", "notes.py"}
    assert len(summary["files"]["notes.py"]) == 200


@pytest.mark.parametrize("mode, agent", [("fences", "claude"), ("codex", "codex")])
def test_reads_fenced_json_and_codex_events(fake, monkeypatch, mode, agent):
    summary, reason = ask(fake, monkeypatch, mode=mode, agent=agent)
    assert reason is None and summary["files"]["auth.py"] == "Timeout raised"


@pytest.mark.parametrize("mode, reason", [("fail", "claude failed (exit 1)"),
                                          ("garbage", "reply wasn't readable")])
def test_failures_give_a_reason(fake, monkeypatch, mode, reason):
    assert ask(fake, monkeypatch, mode=mode) == (None, reason)


def test_timeout(fake, monkeypatch):
    assert ask(fake, monkeypatch, mode="slow", timeout=1) == (None, "claude timed out")


def test_not_installed(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    assert llm.run("x", "claude", []) == (None, "claude not found")
    assert llm.run("x", None, []) == (None, "no agent found")


def test_pick_agent():
    installed = {"codex"}
    which = lambda name: name if name in installed else None
    assert llm.pick_agent({"agent": "claude"}, which) == "claude"  # the session's own agent
    assert llm.pick_agent({}, which) == "codex"  # else the first one installed
    installed.add("claude")
    assert llm.pick_agent(None, which) == "claude"
    installed.clear()
    assert llm.pick_agent({}, which) is None


def test_diary_has_names_only(repo, fake, monkeypatch):
    ask(fake, monkeypatch, root=repo.root)
    ask(fake, monkeypatch, mode="fail", root=repo.root)
    log = "\n".join(store.read_log(repo.root))
    assert "summary by claude: 2 file(s)" in log
    assert "summary by claude unavailable: claude failed (exit 1)" in log
    assert "Timeout" not in log and "<data>" not in log


def test_hook_start_saves_the_agent(repo, monkeypatch, capsys):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start", "--agent", "codex"])
    assert store.load_table(repo.root)["agent"] == "codex"
    repo.write("app.py", "x = 2\n")  # kept photo: the agent is updated too
    main(["hook", "start", "--agent", "claude"])
    assert store.load_table(repo.root)["agent"] == "claude"
    assert json.dumps(store.load_table(repo.root)).count("x = 2") == 0

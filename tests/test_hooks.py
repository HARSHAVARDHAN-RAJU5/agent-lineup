"""Phase 7: hooks for Claude Code and Codex, and `lineup init`."""

import io
import json

import pytest

from lineup import changes, hooks, store
from lineup.cli import main


def hook(repo, monkeypatch, capsys, event, agent="claude", **fields):
    """Call `lineup hook <event> --agent <agent>` the way the agent does: JSON on stdin."""
    payload = {"session_id": "s1", "cwd": str(repo.root), "hook_event_name": event, **fields}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    capsys.readouterr()
    assert main(["hook", event, "--agent", agent]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out)  # always JSON: Codex's Stop hook requires it


@pytest.fixture
def project(repo, monkeypatch, tmp_path_factory):
    repo.write("auth.py", "TIMEOUT = 30\n")
    repo.write("notes.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))  # the hook finds the repo from the event's cwd
    return repo


# ---- session start ----

def test_session_start_takes_the_photo_and_tells_the_user(project, monkeypatch, capsys):
    out = hook(project, monkeypatch, capsys, "start", source="startup")
    assert out["systemMessage"].startswith("lineup: photo taken at")
    assert "hookSpecificOutput" not in out  # nothing for the agent
    assert store.load_table(project.root)["agent"] == "claude"


@pytest.mark.parametrize("source", ["resume", "fork", "compact", "clear"])
def test_resume_fork_and_compact_keep_uncommitted_work(project, monkeypatch, capsys, source):
    hook(project, monkeypatch, capsys, "start", source="startup")
    project.write("notes.py", "x = 2\n")
    out = hook(project, monkeypatch, capsys, "start", source=source)
    assert "keeping the earlier snapshot, 1 uncommitted file" in out["systemMessage"]


# ---- end of reply ----

def test_stop_is_silent_when_nothing_changed(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    assert hook(project, monkeypatch, capsys, "stop") == {}


def test_stop_shows_one_score_line_for_this_reply(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    project.write("auth.py", "TIMEOUT = 1440\n")
    project.write("notes.py", "x = 2\n")
    out = hook(project, monkeypatch, capsys, "stop", last_assistant_message="done")
    assert out == {"systemMessage": "lineup: 2 files changed (1 HIGH, 1 LOW). Run lineup risky to see them."}
    assert hook(project, monkeypatch, capsys, "stop") == {}  # next reply changed nothing: silent
    project.write("notes.py", "x = 3\n")
    out = hook(project, monkeypatch, capsys, "stop")
    assert out["systemMessage"] == ("lineup: 1 file changed (1 LOW). 2 changed this session. "
                                    "Run lineup risky to see them.")


def test_stop_flags_new_commits_first(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    project.write("auth.py", "TIMEOUT = 1440\n")
    project.git("add", "auth.py")
    project.git("commit", "-q", "-m", "agent commit")
    out = hook(project, monkeypatch, capsys, "stop")
    assert out["systemMessage"] == "lineup: committed: auth.py HIGH. Run lineup risky to see them."
    table = store.load_table(project.root)
    assert table["committed"][0]["path"] == "auth.py"


def test_stop_does_nothing_while_a_stop_hook_is_already_continuing(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    project.write("notes.py", "x = 2\n")
    assert hook(project, monkeypatch, capsys, "stop", stop_hook_active=True) == {}


def test_loop_guard_makes_every_hook_do_nothing(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    project.write("notes.py", "x = 2\n")
    log_before = store.read_log(project.root)
    monkeypatch.setenv("LINEUP_RUNNING", "1")
    for event in ("start", "stop", "prompt"):
        assert hook(project, monkeypatch, capsys, event) == {}
    assert store.read_log(project.root) == log_before  # nothing ran


def test_no_photo_or_no_repo_is_silent(repo, monkeypatch, capsys, tmp_path_factory):
    repo.write("a.py", "x\n")
    repo.commit()
    assert hook(repo, monkeypatch, capsys, "stop") == {}
    outside = tmp_path_factory.mktemp("not_a_repo")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(outside)})))
    capsys.readouterr()
    assert main(["hook", "stop", "--agent", "claude"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_a_hook_problem_never_breaks_the_agent(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    monkeypatch.setattr(changes, "find_changes", lambda *a, **k: 1 / 0)
    out = hook(project, monkeypatch, capsys, "stop")
    assert out["systemMessage"].startswith("lineup: hook skipped (ZeroDivisionError")


def test_bad_or_empty_stdin_is_fine(project, monkeypatch, capsys):
    monkeypatch.chdir(project.root)
    for text in ("", "not json", "[1, 2]"):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        capsys.readouterr()
        assert main(["hook", "stop", "--agent", "codex"]) == 0
        json.loads(capsys.readouterr().out)


# ---- the rollback note for the agent ----

def test_rollback_note_is_given_to_the_agent_once(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    project.write("auth.py", "TIMEOUT = 1440\n")
    monkeypatch.chdir(project.root)
    main(["risky"])
    file_id = str(store.load_table(project.root)["files"]["auth.py"]["id"])
    main(["rollback", file_id])
    out = hook(project, monkeypatch, capsys, "prompt", prompt="carry on")
    assert out == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": "Rolled back by the user: auth.py. Read it again before editing."}}
    assert hook(project, monkeypatch, capsys, "prompt", prompt="next") == {}  # delivered once


def test_rollback_note_survives_into_a_new_session(project, monkeypatch, capsys):
    hook(project, monkeypatch, capsys, "start")
    project.write("auth.py", "TIMEOUT = 1440\n")
    project.write("notes.py", "x = 2\n")
    monkeypatch.chdir(project.root)
    main(["risky"])
    ids = {p: str(r["id"]) for p, r in store.load_table(project.root)["files"].items() if "id" in r}
    main(["rollback", ids["auth.py"], ids["notes.py"]])
    out = hook(project, monkeypatch, capsys, "start", source="startup")
    assert out["hookSpecificOutput"]["additionalContext"] == \
        "Rolled back by the user: auth.py, notes.py. Read them again before editing."



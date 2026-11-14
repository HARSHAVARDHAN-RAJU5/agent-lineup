"""Scope: a sensitive file changed although your request never mentioned it. Words only, no AI."""

import io
import json

import pytest

from lineup import scope, store, usage
from lineup.cli import main

KEY = "sk-live-SCOPEKEY1234567890abcdef"


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def hook(monkeypatch, capsys, repo, event, **fields):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo.root), **fields})))
    capsys.readouterr()
    main(["hook", event, "--agent", "claude"])
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else {}


def say(monkeypatch, capsys, repo, prompt):
    hook(monkeypatch, capsys, repo, "prompt", prompt=prompt)


def stop(monkeypatch, capsys, repo):
    return hook(monkeypatch, capsys, repo, "stop").get("systemMessage", "")


def ids(repo):
    return {p: r["id"] for p, r in store.load_table(repo.root)["files"].items() if "id" in r}


@pytest.fixture
def project(repo, monkeypatch, capsys):
    repo.write("config.yaml", "debug: false\n")
    repo.write("src/button.js", "color = 'red'\n")
    repo.write("auth/session.py", "TIMEOUT = 30\n")
    repo.write("auth/login.py", "def login():\n    return True\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    hook(monkeypatch, capsys, repo, "start", source="startup")
    return repo


# ---- the words ----

def test_words_stem_and_hide_secrets():
    assert scope.words("Fix the Settings and dependencies") == {"fix", "the", "setting", "and", "dependency"}
    assert not any("sk" in w and "live" in w for w in scope.words(f"use {KEY} please"))


@pytest.mark.parametrize("text", ["yes", "ok go ahead", "Yes, go ahead!", "sounds good, continue", "do it", "", "lgtm"])
def test_go_aheads(text):
    assert scope.is_go_ahead(scope.words(text))


@pytest.mark.parametrize("text", ["yes, and also update the database", "go fix config", "ok now the login page"])
def test_real_requests_are_not_go_aheads(text):
    assert not scope.is_go_ahead(scope.words(text))


# ---- which request counts ----

def test_go_ahead_steps_back_to_the_real_request(repo):
    for prompt in ("make the login button blue", "yes go ahead"):
        scope.remember(repo.root, prompt)
    assert "login" in scope.take_request(repo.root)


def test_real_last_message_is_used_alone(repo):
    for prompt in ("fix the config loader", "make the login button blue"):
        scope.remember(repo.root, prompt)
    request = scope.take_request(repo.root)
    assert "login" in request and "config" not in request


def test_back_at_the_mark_the_marked_request_is_reused(repo):
    scope.remember(repo.root, "fix the config loader")
    assert "config" in scope.take_request(repo.root)  # the mark is set here
    scope.remember(repo.root, "ok")
    scope.remember(repo.root, "continue")
    assert "config" in scope.take_request(repo.root)  # only go-aheads since: carry on the marked request


def test_never_steps_back_past_the_mark(repo):
    scope.remember(repo.root, "update auth")
    scope.take_request(repo.root)
    scope.remember(repo.root, "fix the config loader")
    scope.take_request(repo.root)  # the mark moves to the config request
    scope.remember(repo.root, "yes")
    request = scope.take_request(repo.root)
    assert "config" in request and "auth" not in request


def test_no_message_seen_means_no_check(repo):
    assert scope.take_request(repo.root) is None


# ---- end of reply ----

def test_out_of_scope_file_is_high_with_a_note_once(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    project.write("src/button.js", "color = 'blue'\n")
    project.write("config.yaml", "debug: true\n")
    message = stop(monkeypatch, capsys, project)
    i = ids(project)
    assert message.splitlines()[1:] == [  # [0] is the session-start line, shown once on top
        "⚠ ≈ lineup: 1 HIGH",
        f"  {i['config.yaml']} config.yaml  outside your request (scope)",
        "  scope: config.yaml changed, but your request didn't mention config",
        f"Undo: lineup rollback {i['config.yaml']}",
    ]
    say(monkeypatch, capsys, project, "now make it bold")
    project.write("src/button.js", "color = 'blue'\nbold = True\n")
    message = stop(monkeypatch, capsys, project)
    assert "scope" not in message and message.startswith("≈ lineup: 1 file changed (1 LOW)")  # mentioned once only


def test_risky_keeps_showing_it_with_a_scope_box(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project)
    _, out = run(capsys, "risky")
    table, box = out.split("Scope (changed")
    assert "config.yaml │ HIGH │ config changed" in table and "+ outside your request (scope)" in table
    assert "config.yaml │ your request didn't mention config" in box
    _, again = run(capsys, "risky")
    assert "Scope (changed" in again  # every time, until dismissed


def test_no_scope_box_without_hits(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "change the config debug flag")
    project.write("config.yaml", "debug: true\n")
    project.write("src/button.js", "color = 'blue'\n")
    stop(monkeypatch, capsys, project)
    _, out = run(capsys, "risky")
    assert "Scope" not in out and "(scope)" not in out


def test_file_path_words_count_as_mentioned(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "fix the timeout in session")
    project.write("auth/session.py", "TIMEOUT = 60\n")
    assert "scope" not in stop(monkeypatch, capsys, project)


def test_yes_go_ahead_uses_the_request_before_it(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    stop(monkeypatch, capsys, project)  # the agent only asked a question: nothing changed, nothing checked
    say(monkeypatch, capsys, project, "yes go ahead")
    project.write("auth/login.py", "def login():\n    return False\n")
    message = stop(monkeypatch, capsys, project)
    assert "scope" not in message  # still HIGH as an auth file, but in scope


def test_continue_carries_on_the_marked_request(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "fix the config loader")
    project.write("config.yaml", "debug: true\n")
    assert "scope" not in stop(monkeypatch, capsys, project)
    say(monkeypatch, capsys, project, "ok continue")
    project.write("auth/session.py", "TIMEOUT = 1\n")
    message = stop(monkeypatch, capsys, project)
    assert "scope: auth/session.py changed, but your request didn't mention auth" in message


def test_ordinary_code_is_never_scope_checked(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "update the readme")
    project.write("src/button.js", "color = 'green'\n")
    project.write("src/new_helper.py", "x = 1\n")
    message = stop(monkeypatch, capsys, project)
    assert "scope" not in message and "HIGH" not in message


def test_no_message_seen_means_no_scope_flags(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    assert "scope" not in stop(monkeypatch, capsys, project)


# ---- dismiss ----

def test_dismiss_puts_the_rule_score_back_until_the_file_changes_again(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project)
    file_id = ids(project)["config.yaml"]
    code, out = run(capsys, "dismiss", str(file_id))
    assert code == 0 and f"{file_id}   config.yaml  scope dismissed. Risk back to MED (config changed)." in out
    _, risky = run(capsys, "risky")
    assert "Scope (changed" not in risky and "config.yaml │ MED" in risky
    say(monkeypatch, capsys, project, "make it bold")  # another reply, config.yaml untouched: stays dismissed
    project.write("src/button.js", "bold = True\n")
    assert "scope" not in stop(monkeypatch, capsys, project)
    project.write("config.yaml", "debug: maybe\n")  # changed again: checked fresh
    assert "scope: config.yaml changed" in stop(monkeypatch, capsys, project)


def test_dismiss_by_name(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project)
    code, out = run(capsys, "dismiss", "config.yaml")
    assert code == 0 and "scope dismissed" in out


def test_dismiss_without_a_flag_changes_nothing(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    project.write("config.yaml", "debug: true\n")
    project.write("src/button.js", "color = 'blue'\n")
    stop(monkeypatch, capsys, project)
    code, out = run(capsys, "dismiss", "src/button.js", "config.yaml")
    assert code == 1 and "src/button.js has no scope flag to dismiss." in out and "Nothing was dismissed." in out
    assert not store.load_table(project.root)["files"]["config.yaml"]["scope"].get("dismissed")
    code, out = run(capsys, "dismiss", "nope.py")
    assert code == 1 and "No changed file named nope.py." in out


# ---- privacy and counting ----

def test_requests_are_words_only_never_logged_and_forgotten_next_session(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, f"make the login button blue, key {KEY}")
    saved = (project.root / ".git/lineup" / store.REQUESTS).read_text(encoding="utf-8")
    assert KEY not in saved and "login" in saved
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project)
    for log in (usage.LOG, store.DIARY):
        text = (project.root / ".git/lineup" / log).read_text(encoding="utf-8")
        assert "login" not in text and KEY not in text and "button" not in text, log
    say(monkeypatch, capsys, project, "fix the login page")
    hook(monkeypatch, capsys, project, "start", source="startup")  # a new conversation
    assert store.load_requests(project.root) == {}


def test_scope_hits_and_dismisses_are_counted_by_type(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the login button blue")
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project)
    run(capsys, "dismiss", "config.yaml")
    events = usage.read(project.root)
    reply = [e for e in events if e["event"] == "reply"][0]
    assert {"rule": "scope", "risk": "HIGH", "type": "config"} in reply["rules"]
    assert {"event": "dismiss", "kind": "scope", "type": "config"}.items() <= [e for e in events if e["event"] == "dismiss"][0].items()
    run(capsys, "report", "--export")
    text = (project.root / "lineup-notes.md").read_text(encoding="utf-8")
    assert "| Flags dismissed as wrong | scope 1, claims 0 |" in text and "| scope | HIGH | config | 1 |" in text
    assert "config.yaml" not in text and "login" not in text

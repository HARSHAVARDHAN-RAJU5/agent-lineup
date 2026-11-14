"""Claims: what the agent said it changed vs what really changed. File names only, no AI."""

import io
import json
import re

import pytest

from lineup import claims, store, usage
from lineup.cli import main

KEY = "sk-live-CLAIMSKEY1234567890abcdef"


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


def stop(monkeypatch, capsys, repo, message):
    fields = {} if message is ... else {"last_assistant_message": message}
    return hook(monkeypatch, capsys, repo, "stop", **fields).get("systemMessage", "")


def ids(repo):
    return {p: r["id"] for p, r in store.load_table(repo.root)["files"].items() if "id" in r}


@pytest.fixture
def project(repo, monkeypatch, capsys):
    repo.write("config.yaml", "debug: false\n")
    repo.write("src/button.js", "color = 'red'\n")
    repo.write("src/utils.py", "x = 1\n")
    repo.write("auth/session.py", "TIMEOUT = 30\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    hook(monkeypatch, capsys, repo, "start", source="startup")
    say(monkeypatch, capsys, repo, "change the config and the button")  # in scope: only claims can fire
    return repo


# ---- reading the message ----

@pytest.mark.parametrize("text, path", [
    ("Updated `config.yaml` to turn debug on.", "config.yaml"),
    ("I edited CONFIG.YAML", "config.yaml"),
    ("Changed auth/session.py", "auth/session.py"),
    ("Changed auth\\session.py on Windows", "auth/session.py"),
    ("Fixed session.py.", "auth/session.py"),
])
def test_mentioned(text, path):
    assert claims.mentioned(path, claims.clean(text))


@pytest.mark.parametrize("text, path", [
    ("Updated the config loader", "config.yaml"),
    ("Updated old_config.yaml", "config.yaml"),
    ("Updated config.yaml.bak", "config.yaml"),
])
def test_not_mentioned(text, path):
    assert not claims.mentioned(path, claims.clean(text))


@pytest.mark.parametrize("text, names", [
    ("I updated utils.py and src/button.js.", ["utils.py", "src/button.js"]),
    ("Fixed the bug in `./src/utils.py`", ["src/utils.py"]),
    ("I looked at utils.py and it's fine.", []),
    ("I didn't change utils.py.", []),
    ("You could update utils.py later.", []),
    ("Updated the docs, see https://example.com/utils.py for more.", []),
])
def test_claimed(text, names):
    assert claims.claimed(claims.clean(text)) == names


def test_no_message_means_nothing_to_read():
    assert claims.clean(None) is None and claims.clean("   ") is None


# ---- check 1: changed, but the agent didn't mention it ----

def test_unmentioned_sensitive_file_is_high_with_a_note_once(project, monkeypatch, capsys):
    project.write("src/button.js", "color = 'blue'\n")
    project.write("config.yaml", "debug: true\n")
    message = stop(monkeypatch, capsys, project, "Made the button blue in src/button.js.")
    i = ids(project)
    assert message.splitlines()[1:] == [
        "⚠ ≈ lineup: 1 HIGH",
        f"  {i['config.yaml']} config.yaml  not mentioned by the agent (claims)",
        "  claims: config.yaml changed, but the agent didn't mention it",
        f"Undo: lineup rollback {i['config.yaml']}",
    ]
    project.write("src/button.js", "color = 'blue'\nbold = True\n")
    message = stop(monkeypatch, capsys, project, "Made it bold.")
    assert "claims" not in message  # mentioned once only


def test_mentioned_file_is_not_flagged(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    message = stop(monkeypatch, capsys, project, "Turned debug on in config.yaml.")
    assert "claims" not in message and "MED" in message


def test_ordinary_code_is_never_checked(project, monkeypatch, capsys):
    project.write("src/button.js", "color = 'green'\n")
    assert "claims" not in stop(monkeypatch, capsys, project, "Done!")


@pytest.mark.parametrize("message", [..., None, ""])
def test_no_message_means_no_check(project, monkeypatch, capsys, message):
    project.write("config.yaml", "debug: true\n")
    assert "claims" not in stop(monkeypatch, capsys, project, message)


def test_changed_again_is_checked_fresh(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    assert "claims: config.yaml" in stop(monkeypatch, capsys, project, "Done.")
    project.write("config.yaml", "debug: maybe\n")
    assert "claims" not in stop(monkeypatch, capsys, project, "Set debug to maybe in config.yaml.")
    assert "claims" not in store.load_table(project.root)["files"]["config.yaml"]


# ---- check 2: the agent said it changed a file, but it didn't ----

def test_said_changed_but_unchanged_is_a_note_only(project, monkeypatch, capsys):
    project.write("src/button.js", "color = 'blue'\n")
    message = stop(monkeypatch, capsys, project, "Made the button blue in button.js and updated utils.py too.")
    assert message.splitlines()[1:] == [
        "≈ lineup: 1 file changed (1 LOW). Run lineup risky to see them.",
        "  claims: the agent said it changed utils.py, but it didn't",
    ]


def test_names_not_in_the_repo_are_ignored(project, monkeypatch, capsys):
    project.write("src/button.js", "color = 'blue'\n")
    assert "claims" not in stop(monkeypatch, capsys, project, "Updated button.js and created newthing.py.")


def test_files_changed_earlier_this_session_count_as_changed(project, monkeypatch, capsys):
    project.write("src/utils.py", "x = 2\n")
    stop(monkeypatch, capsys, project, "Updated utils.py.")
    project.write("src/button.js", "color = 'blue'\n")
    assert "claims" not in stop(monkeypatch, capsys, project, "Updated button.js, after updating utils.py earlier.")


# ---- privacy and counting ----

def test_the_message_is_never_saved_or_logged(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project, f"Secret plan: rotate {KEY}. Updated utils.py.")
    folder = project.root / ".git/lineup"
    for file in folder.iterdir():
        if file.is_file():
            text = file.read_text(encoding="utf-8", errors="replace")
            assert KEY not in text and "Secret plan" not in text and "rotate" not in text, file.name
    for log in (usage.LOG, store.DIARY):
        assert "utils.py" not in (folder / log).read_text(encoding="utf-8"), log


def test_claims_are_counted_by_type(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    project.write("src/button.js", "color = 'blue'\n")
    stop(monkeypatch, capsys, project, "Updated button.js and utils.py.")
    reply = [e for e in usage.read(project.root) if e["event"] == "reply"][0]
    assert {"rule": "claims", "risk": "HIGH", "type": "config"} in reply["rules"]
    assert reply["claims_unchanged"] == 1


# ---- risky and dismiss ----

def test_risky_shows_a_claims_box(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    project.write("src/button.js", "color = 'blue'\n")
    stop(monkeypatch, capsys, project, "Updated button.js and utils.py.")
    _, out = run(capsys, "risky")
    table, box = out.split("Claims (what the agent said")
    assert re.search(r"config\.yaml +│ HIGH │ config changed", table)
    assert "+ not mentioned by the agent (claims)" in table
    assert re.search(r"config\.yaml +│ changed, but the agent didn't mention it", box)
    assert re.search(r"utils\.py +│ the agent said it changed this, but it didn't", box)
    assert "Scope" not in out


def test_no_claims_box_without_hits(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project, "Turned debug on in config.yaml.")
    _, out = run(capsys, "risky")
    assert "Claims" not in out and "(claims)" not in out


def test_dismiss_puts_the_rule_score_back(project, monkeypatch, capsys):
    project.write("config.yaml", "debug: true\n")
    stop(monkeypatch, capsys, project, "Done.")
    file_id = ids(project)["config.yaml"]
    code, out = run(capsys, "dismiss", str(file_id))
    assert code == 0 and f"{file_id}   config.yaml  claims dismissed. Risk back to MED (config changed)." in out
    _, risky = run(capsys, "risky")
    assert "Claims" not in risky and re.search(r"config\.yaml +│ MED", risky)
    dismissed = [e for e in usage.read(project.root) if e["event"] == "dismiss"]
    assert [(e["kind"], e["type"]) for e in dismissed] == [("claims", "config")]


def test_one_dismiss_clears_scope_and_claims(project, monkeypatch, capsys):
    say(monkeypatch, capsys, project, "make the button blue")
    project.write("auth/session.py", "TIMEOUT = 1\n")
    message = stop(monkeypatch, capsys, project, "Made the button blue.")
    assert "scope: auth/session.py" in message and "claims: auth/session.py" in message
    code, out = run(capsys, "dismiss", "auth/session.py")
    assert code == 0 and "scope and claims dismissed. Risk back to HIGH (auth or payment file changed)." in out
    run(capsys, "report", "--export")
    text = (project.root / "lineup-notes.md").read_text(encoding="utf-8")
    assert "| Flags dismissed as wrong | scope 1, claims 1 |" in text and "session.py" not in text

"""Clearer output: the HIGH list after a reply, IDs in diff, rollback by name, removed assertions,
the session-start note, and the README section about `!`."""

import io
import json
import sys
from pathlib import Path

import pytest

from lineup import hooks, store
from lineup.cli import main


@pytest.fixture
def project(repo, monkeypatch):
    repo.write("auth.py", "TIMEOUT = 30\n")
    repo.write("test_utils.py", "from utils import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    repo.write("utils.py", "def add(a, b):\n    return a + b\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    # the agent's change
    repo.write("auth.py", "TIMEOUT = 1440\n")
    repo.write("test_utils.py", "from utils import add\n\ndef test_add():\n    pass\n")
    repo.write("utils.py", "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n")
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def ids(repo):
    return {p: r["id"] for p, r in store.load_table(repo.root)["files"].items() if "id" in r}


# ---- 1. the score message lists HIGH files ----

def test_stop_lists_each_high_file_with_its_id_and_how_to_undo(project, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(project.root)})))
    _, out = run(capsys, "hook", "stop", "--agent", "claude")
    message = json.loads(out)["systemMessage"]
    i = ids(project)
    assert message.splitlines() == [
        "⚠ ≈ lineup: 2 HIGH",
        f"  {i['auth.py']} auth.py        auth or payment file changed",
        f"  {i['test_utils.py']} test_utils.py  assertion removed",
        f"Undo: lineup rollback {i['auth.py']}",
    ]


def test_more_than_five_high_files_are_summed_up():
    message = hooks.high_list([(n, f"auth{n}.py", "auth or payment file changed") for n in range(1, 9)])
    lines = message.splitlines()
    assert lines[0] == "⚠ ≈ lineup: 8 HIGH" and len(lines) == 1 + 5 + 1 + 1
    assert lines[-2] == "  +3 more" and lines[-1] == "Undo: lineup rollback 1"


def test_no_high_keeps_the_one_line_count(repo, monkeypatch, capsys):
    repo.write("notes.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    repo.write("notes.py", "x = 2\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo.root)})))
    _, out = run(capsys, "hook", "stop", "--agent", "claude")
    assert json.loads(out) == {"systemMessage": "≈ lineup: 1 file changed (1 LOW). Run lineup risky to see them."}


# ---- 2. IDs in diff ----

FAKE_AGENT = r'''
import json, re, sys
paths = re.findall(r"^--- (\S+) \(", sys.stdin.read(), re.M)
print(json.dumps({"result": json.dumps({"overview": "ok", "files": {p: "line for " + p for p in paths}})}))
'''


def test_diff_tables_show_the_same_ids_as_risky(project, capsys, monkeypatch, tmp_path_factory):
    from lineup import llm
    script = tmp_path_factory.mktemp("fake") / "agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    monkeypatch.setitem(llm.AGENTS, "claude", ["fake-claude", str(script)])
    monkeypatch.setattr(llm, "installed", lambda c: sys.executable if c == "fake-claude" else None)
    _, out = run(capsys, "diff")
    i = ids(project)
    assert "│ ID │ File" in out and "│ Group     │ ID │ Name" in out
    rows = [line for line in out.splitlines() if line.startswith("│")]
    assert any(f"│ {i['auth.py']}  │ auth.py" in r and "line for auth.py" in r for r in rows)  # AI table
    assert any(f"│ {i['auth.py']}  │ auth.py" in r and "auth, +1 -1" in r for r in rows)  # groups table
    assert any(f"│ {i['utils.py']}  │ multiply" in r for r in rows)  # a function row shows its file's ID


# ---- 3. rollback by name ----

def test_rollback_by_file_name(project, capsys):
    run(capsys, "risky")
    code, out = run(capsys, "rollback", "auth.py")
    assert code == 0 and "auth.py  restored" in out
    assert (project.root / "auth.py").read_text() == "TIMEOUT = 30\n"


def test_exact_path_wins_and_full_paths_work(project, capsys):
    project.write("src/auth.py", "A = 1\n")
    run(capsys, "risky")
    code, out = run(capsys, "rollback", "auth.py", "src/auth.py")
    assert code == 0  # "auth.py" is the root file exactly; "src/auth.py" names the other one
    assert (project.root / "auth.py").read_text() == "TIMEOUT = 30\n"
    assert not (project.root / "src/auth.py").exists()


def test_name_matching_several_files_changes_nothing(repo, monkeypatch, capsys):
    repo.write("lib/auth.py", "A = 1\n")
    repo.write("src/auth.py", "B = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    repo.write("lib/auth.py", "A = 2\n")
    repo.write("src/auth.py", "B = 2\n")
    run(capsys, "risky")
    i = ids(repo)
    code, out = run(capsys, "rollback", "auth.py")
    assert code == 1
    assert (f"auth.py matches more than one file: {i['lib/auth.py']} lib/auth.py, {i['src/auth.py']} src/auth.py. "
            "Use the ID.") in out and "Nothing was changed." in out
    assert (repo.root / "lib/auth.py").read_text() == "A = 2\n" and (repo.root / "src/auth.py").read_text() == "B = 2\n"


def test_unknown_name_changes_nothing(project, capsys):
    run(capsys, "risky")
    code, out = run(capsys, "rollback", "auth.py", "nope.py")
    assert code == 1 and "No changed file named nope.py." in out and "Nothing was changed." in out
    assert (project.root / "auth.py").read_text() == "TIMEOUT = 1440\n"


# ---- 4. removed assertions are listed ----

def test_removals_list_removed_assertions(project, capsys):
    _, out = run(capsys, "diff")
    removals = out.split("│ Removals")[1].split("├")[0]
    assert "assertion" in removals and "1 removed (test_utils.py)" in removals
    assert "nothing" not in removals


# ---- 5. the session-start note ----

def test_init_preview_shows_the_note_and_remove_says_it_stops(repo, monkeypatch, capsys):
    repo.write("a.py", "x\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    _, out = run(capsys, "init", "--yes", "--agent", "claude")
    assert "At each session start, the agent is also given this note:" in out
    assert "Don't comment on or evaluate that output unless the user asks" in " ".join(out.split())
    _, out = run(capsys, "init", "--remove", "--yes")
    assert "no longer gets lineup's session-start note" in out
    assert not hooks.installed(repo.root)


def test_note_text_is_exact():
    assert hooks.SESSION_NOTE == (
        "This repo uses lineup, a tool that reviews agent changes. The user may run lineup diff, lineup risky or "
        "lineup rollback with !. Don't comment on or evaluate that output unless the user asks. Its scores are "
        "meant as a check that's independent of the agent. Rollback takes IDs or file names shown by lineup.")


# ---- 6. README ----

def test_readme_explains_where_to_run_lineup():
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    assert "with `!` in the same session" in readme and "second terminal" in readme
    assert "Its commentary isn't part of lineup's check" in readme

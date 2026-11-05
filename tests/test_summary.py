"""Phase 4 step 3: saved answers. The fake agent counts how often it's called."""

import json
import sys

import pytest

from lineup import snapshot, store
from lineup.changes import find_changes
from lineup.guard import ai_input
from lineup.summary import summarize

FAKE_AGENT = r'''
import json, os, re, sys
message = sys.stdin.read()
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as calls:
    calls.write(json.dumps(message) + "\n")
if os.environ.get("FAKE_MODE") == "fail":
    sys.exit(1)
paths = re.findall(r"^--- (\S+) \(", message, re.M)
files = {p: "summary of " + p for p in paths}
print(json.dumps({"result": json.dumps({"overview": "overview of " + ", ".join(paths), "files": files})}))
'''


@pytest.fixture
def setup(repo, tmp_path_factory, monkeypatch):
    outside = tmp_path_factory.mktemp("fake")  # outside the repo
    script = outside / "fake_agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    calls = outside / "calls.txt"
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    repo.write("session.py", "TIMEOUT = 30\n")
    repo.write("cache.py", "x = 1\n")
    repo.write("routes.py", "y = 1\n")
    repo.commit()
    snapshot.take(repo.root)
    repo.write("session.py", "TIMEOUT = 1440\n")
    repo.write("cache.py", "x = 2\n")

    def run():
        found = find_changes(repo.root, store.load_table(repo.root))
        items, _ = ai_input(found, {c.path: "LOW" for c in found})
        return summarize(repo.root, items, "claude", command=[sys.executable, str(script)])

    def sent():
        if not calls.is_file():
            return []
        return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]

    return repo, run, sent


def test_second_run_makes_no_ai_call(setup):
    repo, run, sent = setup
    first = run()
    second = run()
    assert len(sent()) == 1
    assert first["asked"] == ["cache.py", "session.py"] and second["asked"] == []
    assert second["files"] == first["files"] and second["overview"] == first["overview"]


def test_only_the_edited_file_is_sent_again(setup):
    repo, run, sent = setup
    run()
    repo.write("routes.py", "y = 2\n")
    result = run()
    assert result["asked"] == ["routes.py"]
    message = sent()[1]
    assert "+y = 2" in message
    assert "+TIMEOUT = 1440" not in message  # old file: only its saved line goes
    assert "earlier summary: summary of session.py" in message
    assert set(result["files"]) == {"cache.py", "routes.py", "session.py"}


def test_file_put_back_loses_its_line(setup):
    repo, run, sent = setup
    run()
    repo.write("cache.py", "x = 1\n")
    result = run()
    assert set(result["files"]) == {"session.py"}
    assert set(store.load_summaries(repo.root)["files"]) == {"session.py"}


def test_fresh_photo_clears_saved_answers(setup):
    repo, run, sent = setup
    run()
    snapshot.take(repo.root)
    assert store.load_summaries(repo.root) == {"overview": {}, "files": {}}


def test_saved_file_has_keys_and_lines_but_no_diff(setup):
    repo, run, sent = setup
    repo.write(".env", "SECRET=abc123\n")
    run()
    text = json.dumps(store.load_summaries(repo.root))
    assert "TIMEOUT" not in text and "1440" not in text and "abc123" not in text
    assert "summary of session.py" in text


def test_failed_ai_keeps_old_lines(setup, monkeypatch):
    repo, run, sent = setup
    run()
    repo.write("routes.py", "y = 2\n")
    monkeypatch.setenv("FAKE_MODE", "fail")
    result = run()
    assert result["reason"] == "claude failed (exit 1)"
    assert set(result["files"]) == {"cache.py", "session.py"}  # routes.py: no summary yet
    monkeypatch.delenv("FAKE_MODE")
    assert run()["asked"] == ["routes.py"]  # asked again next time

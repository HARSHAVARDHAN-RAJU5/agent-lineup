"""lineup report: counts and dates only, from local data. No code, file names or secrets."""

import io
import json
import re

from lineup import store, usage
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
    saved = (repo.root / ".git/lineup" / usage.LOG).read_text(encoding="utf-8")
    assert all(leak not in saved for leak in ("auth.py", "notes.py", "TIMEOUT", SECRET))


def test_report_ignores_unknown_or_bad_entries(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    log = store.folder(repo.root) / usage.LOG
    log.write_text("\n".join([
        json.dumps({"t": "2026-11-10T10:00:00", "event": "session_start", "agent": "claude"}),
        json.dumps({"t": "2026-11-10T11:00:00", "event": "session_start", "agent": "auth.py"}),
        "not json",
        json.dumps(["not", "an", "event"]),
        json.dumps({"t": "2026-11-10T12:00:00", "event": "command", "name": 7}),
    ]) + "\n", encoding="utf-8")
    _, out = run(capsys, "report")
    assert re.search(r"Sessions started\s+2\n", out)


def test_broken_log_never_breaks_a_command(repo, monkeypatch, capsys):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    (store.folder(repo.root) / usage.LOG).write_bytes(b"\xff\xfe not json at all\n")
    code, _ = run(capsys, "risky")
    assert code == 0
    _, out = run(capsys, "report")
    assert re.search(r"lineup risky runs\s+1\n", out)


# ---- the usage log itself ----

def events(repo, name=None):
    return [e for e in usage.read(repo.root) if name is None or e["event"] == name]


def test_log_records_types_never_names(repo, monkeypatch, capsys):
    repo.write("zebra_invoice_xyz.py", "RATE = 1\n")
    repo.write("tests/test_billing.py", "def test_rate():\n    assert True\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    hook(monkeypatch, repo, "start")
    repo.write("zebra_invoice_xyz.py", "RATE = 2\nUNIQUE_CODE_STRING_42 = 1\n")
    repo.write("tests/test_billing.py", "def test_rate():\n    pass\n")
    repo.write(".env", f"KEY={SECRET}\n")
    hook(monkeypatch, repo, "stop")
    run(capsys, "rollback", "zebra_invoice_xyz.py")
    reply = events(repo, "reply")[0]
    assert reply["files_changed"] == 3 and reply["agent"] == "claude"
    assert {r["type"] for r in reply["rules"]} == {"python", "test", "env"}
    assert {"rule": "assertion_removed", "risk": "HIGH", "type": "test"} in reply["rules"]
    assert {"rule": "private_file", "risk": "HIGH", "type": "env"} in reply["rules"]
    start = events(repo, "session_start")[0]
    assert start["agent"] == "claude" and start["os"] and start["python"] and start["lineup"]
    rollback = events(repo, "rollback")[0]
    assert rollback["outcome"] == "done" and rollback["type"] == "python"
    assert isinstance(rollback["seconds_since_flag"], int) and rollback["seconds_since_flag"] >= 0
    text = (repo.root / ".git/lineup" / usage.LOG).read_text(encoding="utf-8")
    for leak in ("zebra_invoice_xyz", "test_billing", "UNIQUE_CODE_STRING_42", SECRET, ".env", "RATE",
                 str(repo.root), "test_rate"):
        assert leak not in text, leak


def test_unsafe_text_never_reaches_the_log(repo):
    usage.record(repo.root, "command", name="auth.py", other="src/x/y.py", ok="python", n=3)
    event = events(repo)[0]
    assert event["name"] == "?" and event["other"] == "?" and event["ok"] == "python" and event["n"] == 3


def test_rule_types_hide_names():
    assert usage.rule_type("function added: login_with_token") == "function_added"
    assert usage.rule_type("possible weakened test, check this (src/billing.py changed too)") == "weakened_test"
    assert usage.rule_type("140 lines removed") == "many_lines_removed"
    assert usage.rule_type("something new") == "other"


def test_log_keeps_working_at_the_size_cap(repo, monkeypatch):
    monkeypatch.setattr(usage, "MAX_BYTES", 2000)
    monkeypatch.setattr(usage, "KEEP_BYTES", 1000)
    for n in range(200):
        usage.record(repo.root, "command", name="risky", n=n)
    log = repo.root / ".git/lineup" / usage.LOG
    assert log.stat().st_size <= 2000
    kept = events(repo)
    assert kept and kept[-1]["n"] == 199  # the newest are kept
    assert all(isinstance(e, dict) for e in kept)  # trimmed at a line break: every line still reads


def test_logging_failure_never_breaks_hooks_or_commands(repo, monkeypatch, capsys):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(usage, "_safe", broken)  # every write to the log fails
    hook(monkeypatch, repo, "start")
    repo.write("a.py", "x = 2\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo.root)})))
    capsys.readouterr()
    assert main(["hook", "stop", "--agent", "claude"]) == 0
    assert "lineup:" in json.loads(capsys.readouterr().out)["systemMessage"]
    for argv in (["risky"], ["diff"], ["report"], ["rollback", "a.py"]):
        assert run(capsys, *argv)[0] == 0, argv


def test_hook_errors_are_logged_by_type_only(repo, monkeypatch, capsys):
    from lineup import hooks
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)

    def crash(*args, **kwargs):
        raise ValueError("bad thing in zebra_invoice_xyz.py")

    monkeypatch.setattr(hooks, "on_stop", crash)
    hook(monkeypatch, repo, "stop")
    error = events(repo, "hook_error")[0]
    assert error["hook"] == "stop" and error["error"] == "ValueError"
    assert "secret_billing" not in (repo.root / ".git/lineup" / usage.LOG).read_text(encoding="utf-8")


def test_ai_summary_time_tokens_and_failures(repo):
    from lineup import llm
    import sys
    ok = [sys.executable, "-c", "import json;print(json.dumps({'result': json.dumps({'overview': 'x', 'files': {}}),"
                                "'usage': {'input_tokens': 100, 'output_tokens': 20, 'cache_read_input_tokens': 5}}))"]
    llm.run("hi", "claude", [], root=repo.root, command=ok)
    fail = [sys.executable, "-c", "import sys; sys.exit(3)"]
    llm.run("hi", "claude", [], root=repo.root, command=fail)
    slow = [sys.executable, "-c", "import time; time.sleep(5)"]
    llm.run("hi", "claude", [], root=repo.root, command=slow, timeout=0.5)
    first, second, third = events(repo, "ai_summary")
    assert first["result"] == "ok" and first["tokens"] == 120 and isinstance(first["seconds"], float)
    assert second["result"] == "failed" and second["tokens"] is None
    assert third["result"] == "timeout"


def test_init_and_remove_are_logged(repo, monkeypatch, capsys):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    run(capsys, "init", "--yes", "--agent", "claude")
    run(capsys, "init", "--remove", "--yes")
    assert [e["event"] for e in events(repo)] == ["init", "init_remove"]
    assert events(repo, "init")[0]["agents"] == ["claude"]

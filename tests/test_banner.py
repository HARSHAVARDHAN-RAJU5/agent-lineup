"""The banner: shown on init, --version and no command, only in a real terminal (plain with NO_COLOR).
Never when piped, in hooks, or in output the agent reads."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lineup import banner
from lineup.cli import main

BANNER_FILE = Path(__file__).parent.parent / "src/lineup/lineup-banner.txt"
ESC = "\x1b["


class Terminal(io.StringIO):
    def isatty(self):
        return True


def run(*argv, cwd=None, env_extra=None):
    """Run lineup as a real process with piped output (as a tester's pipe or the agent would)."""
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "LINEUP_RUNNING")}
    env.update(PYTHONIOENCODING="utf-8", **(env_extra or {}))
    done = subprocess.run([sys.executable, "-m", "lineup", *argv], cwd=cwd, env=env,
                          capture_output=True, input=b"", timeout=60)
    return done.returncode, done.stdout.decode("utf-8").replace("\r\n", "\n")


def in_terminal(monkeypatch, capsys, *argv):
    """Run lineup as if its output were a real terminal."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(banner, "_windows_colour_on", lambda stream: False)  # plain, easy to compare
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    code = main(list(argv))
    return code, terminal.getvalue()


def test_banner_is_packaged_and_printed_exactly(monkeypatch, capsys):
    assert banner.text() == BANNER_FILE.read_text(encoding="utf-8")
    code, out = in_terminal(monkeypatch, capsys, "--version")
    assert code == 0 and out == banner.text() + "\n" + "lineup 0.1.0\n"


def test_no_command_shows_banner_then_help_in_a_terminal(monkeypatch, capsys):
    code, out = in_terminal(monkeypatch, capsys)
    assert code == 0 and out.startswith(banner.text()) and "usage: lineup" in out


def test_init_shows_banner_in_a_terminal(repo, monkeypatch, capsys):
    repo.write("a.py", "x\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    code, out = in_terminal(monkeypatch, capsys, "init", "--yes", "--agent", "claude")
    assert code == 0 and out.startswith(banner.text())


def test_piped_version_prints_only_the_version():
    code, out = run("--version")
    assert code == 0 and out == "lineup 0.1.0\n"


def test_piped_output_has_no_banner_and_no_colour_codes(repo):
    repo.write("a.py", "x\n")
    repo.commit()
    for argv in (["--version"], [], ["init", "--yes", "--agent", "claude"]):
        _, out = run(*argv, cwd=repo.root)
        assert ESC not in out and "⣿" not in out and banner.text() not in out, argv
    _, out = run(cwd=repo.root)
    assert out.startswith("usage: lineup")


def test_terminal_gets_light_blue(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setattr(banner, "_windows_colour_on", lambda stream: True)
    out = Terminal()
    banner.show(out)
    assert out.getvalue() == banner.BLUE + banner.text().rstrip("\n") + banner.RESET + "\n\n"
    assert banner.BLUE == "\x1b[38;2;111;140;216m"  # #6F8CD8


@pytest.mark.parametrize("value", ["1", ""])
def test_no_color_means_plain_text_even_in_a_terminal(monkeypatch, value):
    monkeypatch.setenv("NO_COLOR", value)
    monkeypatch.setattr(banner, "_windows_colour_on", lambda stream: True)
    out = Terminal()
    banner.show(out)
    assert out.getvalue() == banner.text() + "\n" and ESC not in out.getvalue()


def test_plain_text_when_colour_isnt_supported(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(banner, "_windows_colour_on", lambda stream: False)
    monkeypatch.setattr(sys, "platform", "win32")
    out = Terminal()
    banner.show(out)
    assert ESC not in out.getvalue()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("TERM", "dumb")
    out = Terminal()
    banner.show(out)
    assert ESC not in out.getvalue()


# ---- never in hooks or in what the agent reads ----

@pytest.fixture
def project(repo, monkeypatch):
    repo.write("auth.py", "TIMEOUT = 30\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    return repo


@pytest.mark.parametrize("event", ["start", "stop", "prompt"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_hook_output_stays_valid_json_without_banner(project, monkeypatch, capsys, event, agent):
    monkeypatch.setattr(banner, "show", lambda *a, **k: pytest.fail("banner shown in a hook"))
    for step in ("start", event):  # a photo first, then the event (with a change for stop)
        project.write("auth.py", "TIMEOUT = 1440\n") if step == "stop" else None
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(project.root), "source": "startup"})))
        capsys.readouterr()
        assert main(["hook", step, "--agent", agent]) == 0
        out = capsys.readouterr().out
        data = json.loads(out)  # the whole output is one JSON object
        assert isinstance(data, dict) and ESC not in out
    if event == "stop":
        assert "≈ lineup:" in data["systemMessage"]


@pytest.mark.parametrize("argv", [["diff"], ["risky"], ["rollback", "auth.py"]])
def test_diff_risky_rollback_never_show_banner(project, monkeypatch, capsys, argv):
    monkeypatch.setattr(banner, "show", lambda *a, **k: pytest.fail("banner shown"))
    main(["hook", "start"])
    project.write("auth.py", "TIMEOUT = 1440\n")
    main(["risky"])
    capsys.readouterr()
    main(argv)
    assert "⣿" not in capsys.readouterr().out  # no braille banner characters

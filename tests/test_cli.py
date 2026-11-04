import pytest

from lineup import __version__
from lineup.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_required():
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("argv", [["init"], ["rollback", "3", "4"]])
def test_commands_parse(argv, capsys):
    assert main(argv) == 1
    assert "not built yet" in capsys.readouterr().out


def test_diff_outside_a_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["diff"]) == 2
    assert "not inside a git repository" in capsys.readouterr().err


def test_diff_lists_changes(repo, monkeypatch, capsys):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("app.py", "x = 2\n")
    monkeypatch.chdir(repo.root)
    assert main(["diff"]) == 0
    out = capsys.readouterr().out
    assert "Edits" in out and "app.py" in out and "+1 -1" in out


def test_diff_with_no_changes(repo, monkeypatch, capsys):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    assert main(["diff"]) == 0
    assert "no changes" in capsys.readouterr().out


def test_risky_lists_scores(repo, monkeypatch, capsys):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["hook", "start"])
    repo.write("app.py", "x = 2\n")
    repo.write("auth.py", "y = 1\n")
    capsys.readouterr()
    assert main(["risky"]) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if ".py" in line]
    assert "auth.py" in rows[0] and "HIGH" in rows[0] and "auth or payment file changed" in rows[0]
    assert "New" in rows[0]
    assert "app.py" in rows[1] and "LOW" in rows[1] and "code edited" in rows[1]


def test_risky_with_no_changes(repo, monkeypatch, capsys):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    assert main(["risky"]) == 0
    assert "no changes" in capsys.readouterr().out


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


@pytest.mark.parametrize("argv", [["init"], ["diff"], ["risky"], ["rollback", "3", "4"]])
def test_commands_parse(argv, capsys):
    assert main(argv) == 1
    assert "not built yet" in capsys.readouterr().out

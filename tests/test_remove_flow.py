"""lineup init --remove: offer the export first, then deleting data (default keeps it); piped does neither."""

import io

import pytest

from lineup import store, usage
from lineup.cli import main


class Keyboard(io.StringIO):
    """Typed answers, one per line, from a terminal."""

    def isatty(self):
        return True


def run(capsys, monkeypatch, *argv, answers=None, piped=False):
    asked = []
    if answers is not None:
        replies = iter(answers)

        def fake_input(prompt=""):
            asked.append(prompt.strip())
            try:
                return next(replies)
            except StopIteration:
                raise EOFError
        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr("sys.stdin", Keyboard())
    elif piped:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err, asked


@pytest.fixture
def project(repo, monkeypatch, capsys):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["init", "--yes", "--agent", "claude"])
    main(["hook", "start"])
    main(["note", "a tester note"])
    capsys.readouterr()
    return repo


def drawer(repo):
    return repo.root / ".git/lineup"


def test_export_is_offered_before_delete_and_enter_keeps_data(project, capsys, monkeypatch):
    code, out, asked = run(capsys, monkeypatch, "init", "--remove", answers=["y", "", ""])
    assert code == 0
    assert asked == ["Remove them? [y/N]", "Export your lineup notes first? [Y/n]",
                     "Also delete lineup's saved data (photo, copies, backups)? [y/N]"]
    assert (project.root / "lineup-notes.md").is_file()  # Enter on the export question = yes
    assert drawer(project).is_dir() and store.has_snapshot(project.root)  # Enter on delete = keep
    assert "saved data stays in .git/lineup/" in out


def test_yes_to_both_exports_then_deletes(project, capsys, monkeypatch):
    code, out, _ = run(capsys, monkeypatch, "init", "--remove", answers=["y", "y", "y"])
    assert code == 0
    notes = (project.root / "lineup-notes.md").read_text(encoding="utf-8")
    assert "a tester note" in notes  # exported before the data went
    assert not drawer(project).exists()
    assert out.index("Wrote") < out.index("Deleted .git/lineup/.")


def test_no_export_but_delete(project, capsys, monkeypatch):
    _, _, asked = run(capsys, monkeypatch, "init", "--remove", answers=["y", "n", "yes"])
    assert len(asked) == 3
    assert not (project.root / "lineup-notes.md").exists() and not drawer(project).exists()


@pytest.mark.parametrize("flags", [["--yes"], []])
def test_piped_or_yes_does_neither(project, capsys, monkeypatch, flags):
    if flags:
        code, out, _ = run(capsys, monkeypatch, "init", "--remove", *flags, piped=True)
        assert code == 0 and "lineup's hooks are removed" in out
    else:  # piped without --yes: nothing is removed at all, so nothing else happens either
        code, out, _ = run(capsys, monkeypatch, "init", "--remove", piped=True)
        assert code == 1 and "Nothing was written" in out
    assert not (project.root / "lineup-notes.md").exists()
    assert drawer(project).is_dir() and store.load_notes(project.root)


def test_declining_the_removal_asks_nothing_else(project, capsys, monkeypatch):
    code, _, asked = run(capsys, monkeypatch, "init", "--remove", answers=["n"])
    assert code == 1 and asked == ["Remove them? [y/N]"] and drawer(project).is_dir()


def test_no_input_after_removal_keeps_everything(project, capsys, monkeypatch):
    code, out, _ = run(capsys, monkeypatch, "init", "--remove", answers=["y"])  # then input ends
    assert code == 0 and drawer(project).is_dir() and not (project.root / "lineup-notes.md").exists()


def test_delete_refuses_a_linked_drawer(repo, monkeypatch):
    from lineup import paths
    store.folder(repo.root)
    monkeypatch.setattr(paths, "is_link", lambda p: True)
    with pytest.raises(OSError, match="is a link"):
        store.delete_all(repo.root)
    assert drawer(repo).is_dir()


def test_no_data_folder_means_no_delete_question(repo, capsys, monkeypatch):
    repo.write("a.py", "x = 1\n")
    repo.commit()
    monkeypatch.chdir(repo.root)
    main(["init", "--yes", "--agent", "claude"])
    store.delete_all(repo.root)
    monkeypatch.setattr(usage, "record", lambda *a, **k: None)  # nothing gets logged this time
    _, _, asked = run(capsys, monkeypatch, "init", "--remove", answers=["y"])
    assert asked == ["Remove them? [y/N]"]

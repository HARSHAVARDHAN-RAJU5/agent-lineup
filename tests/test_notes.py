"""lineup note: saved exactly as typed, except secrets, which become [REDACTED]."""

from lineup import store, usage
from lineup.cli import main

KEY = "sk-live-NOTEKEY1234567890abcdef"
STRIPE = "sk_live_NOTE4567890abcdefXYZ"


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_note_is_saved_exactly_as_typed(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    code, out = run(capsys, "note", "Rollback  put back the WRONG version, très bizarre!")
    assert code == 0 and out.strip() == "Note saved."
    [(stamp, text)] = store.load_notes(repo.root)
    assert text == "Rollback  put back the WRONG version, très bizarre!" and stamp[:4].isdigit()


def test_note_words_without_quotes_are_joined(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    run(capsys, "note", "flag", "was", "wrong")
    assert store.load_notes(repo.root)[0][1] == "flag was wrong"


def test_note_with_a_fake_key_is_saved_redacted(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    code, out = run(capsys, "note", f"agent pasted {KEY} into config")
    assert code == 0
    assert "1 possible secret was replaced with [REDACTED] before saving." in out
    assert store.load_notes(repo.root)[0][1] == "agent pasted [REDACTED] into config"
    code, out = run(capsys, "note", f"keys {KEY} and {STRIPE}, password=hunter2x")
    assert "3 possible secrets were replaced with [REDACTED] before saving." in out
    saved = (repo.root / ".git/lineup" / store.NOTES).read_text(encoding="utf-8")
    assert KEY not in saved and STRIPE not in saved and "hunter2x" not in saved


def test_note_without_text_shows_usage(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    code, out = run(capsys, "note")
    assert code == 2 and out.strip().startswith('Usage: lineup note "what happened"')
    assert store.load_notes(repo.root) == []


def test_note_list(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    assert "No notes yet" in run(capsys, "note", "--list")[1]
    run(capsys, "note", "first")
    run(capsys, "note", "second one")
    code, out = run(capsys, "note", "--list")
    lines = out.strip().splitlines()
    assert code == 0 and lines[0].endswith("  first") and lines[1].endswith("  second one")


def test_note_runs_are_logged_without_the_text(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    run(capsys, "note", "private thought about billing")
    log = (repo.root / ".git/lineup" / usage.LOG).read_text(encoding="utf-8")
    assert '"name":"note"' in log and "billing" not in log

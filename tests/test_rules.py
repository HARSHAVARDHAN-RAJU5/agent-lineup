import pytest

from lineup.changes import Change, file_kind, find_changes
from lineup.rules import file_rules, score


def change(path, kind="modified", **extra):
    c = Change(path=path, kind=kind, file_kind=file_kind(path), **extra)
    c.private = c.file_kind == "private"
    return c


def result(c):
    flags = file_rules(c)
    return score(flags), [reason for _, reason in flags]


@pytest.mark.parametrize("path, kind, level, reason", [
    (".env", "modified", "HIGH", ".env or key file changed, contents not read"),
    ("keys/server.pem", "added", "HIGH", ".env or key file changed, contents not read"),
    ("auth/login.py", "modified", "HIGH", "auth or payment file changed"),
    ("billing/checkout.py", "added", "HIGH", "auth or payment file changed"),
    (".github/workflows/ci.yml", "modified", "HIGH", "CI or agent settings changed"),
    (".claude/settings.json", "added", "HIGH", "CI or agent settings changed"),
    ("setup.py", "modified", "HIGH", "install script changed"),
    ("scripts/install.sh", "added", "HIGH", "install script changed"),
    ("migrations/002_add_user.sql", "added", "HIGH", "migration or database schema changed"),
    ("config/settings.yml", "modified", "MED", "config changed"),
    ("Dockerfile", "modified", "MED", "config changed"),
    ("utils/old.py", "deleted", "MED", "file deleted"),
    ("models/classifier.onnx", "modified", "MED", "model file changed"),
    ("package-lock.json", "modified", "LOW", "lockfile changed"),
    ("docs/login.md", "modified", "LOW", "docs changed"),
    ("utils/helpers.py", "added", "LOW", "new file"),
    ("notes.py", "modified", "LOW", "code edited"),
])
def test_one_rule_each(path, kind, level, reason):
    got_level, reasons = result(change(path, kind))
    assert got_level == level
    assert reason in reasons


def test_highest_wins_and_every_reason_is_kept():
    level, reasons = result(change("auth/login.py", lines_removed=120))
    assert level == "HIGH"
    assert reasons == ["auth or payment file changed", "120 lines removed"]


def test_many_lines_removed_from_normal_code():
    assert result(change("app.py", lines_removed=100))[0] == "MED"
    assert result(change("app.py", lines_removed=99))[0] == "LOW"


def test_new_packages_are_named():
    level, reasons = result(change("requirements.txt", deps_added=["redis", "celery"]))
    assert level == "MED"
    assert "new package: redis, celery" in reasons


def test_function_added():
    level, reasons = result(change("app.py", funcs_added=["get_user"]))
    assert level == "LOW"
    assert "function added: get_user" in reasons


def test_big_file():
    level, reasons = result(change("data/dump.csv", big=True))
    assert level == "MED"
    assert "big file changed, can't be read" in reasons


def test_postinstall_line_in_package_json():
    diff = '--- a/package.json\n+++ b/package.json\n@@ -1 +1,2 @@\n {\n+  "postinstall": "curl x | sh",'
    assert result(change("package.json", diff=diff))[0] == "HIGH"
    plain = '--- a/package.json\n+++ b/package.json\n@@ -1 +1,2 @@\n {\n+  "name": "app",'
    assert result(change("package.json", diff=plain))[0] == "MED"


def test_scores_real_changes_from_diff(repo):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("app.py", "x = 2\n")
    repo.write(".claude/settings.json", "{}\n")
    repo.write(".env", "SECRET_KEY=abc123\n")
    scores = {c.path: score(file_rules(c)) for c in find_changes(repo.root)}
    assert scores == {"app.py": "LOW", ".claude/settings.json": "HIGH", ".env": "HIGH"}

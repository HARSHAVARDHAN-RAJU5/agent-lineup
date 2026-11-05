import pytest

from lineup import changes
from lineup.changes import file_kind, find_changes, format_groups, sort_into_groups
from lineup.guard import is_private


@pytest.fixture
def login_repo(repo):
    """The example from the architecture page, before Claude's edits."""
    repo.write("auth/session.py", "TIMEOUT_MINUTES = 30\n\ndef refresh_token():\n    pass\n")
    repo.write("api/routes.py", "from auth.session import TIMEOUT_MINUTES\n")
    repo.write("tests/test_session.py",
               "def test_login():\n    assert True\n\ndef test_expired_session():\n    assert False\n")
    repo.write("requirements.txt", "flask==3.0\n")
    repo.write("docs/login.md", "# Login\n")
    repo.commit("start")
    return repo


def claude_edits(repo):
    repo.write("auth/session.py", "TIMEOUT_MINUTES = 1440\n\ndef refresh_token():\n    pass\n")
    repo.write("api/routes.py", "from auth.session import TIMEOUT_MINUTES\nCACHE = True\n")
    repo.write("tests/test_session.py", "def test_login():\n    assert True\n")
    repo.write("requirements.txt", "flask==3.0\nredis>=5\n")
    repo.write("utils/cache.py", "def get(key):\n    return None\n")
    repo.write(".env", "SECRET_KEY=abc123\n")


def by_path(found):
    return {c.path: c for c in found}


def test_no_changes(login_repo):
    assert find_changes(login_repo.root) == []


def test_finds_every_change_in_the_example(login_repo):
    claude_edits(login_repo)
    found = by_path(find_changes(login_repo.root))

    assert set(found) == {"auth/session.py", "api/routes.py", "tests/test_session.py",
                          "requirements.txt", "utils/cache.py", ".env"}
    assert found["utils/cache.py"].kind == "added"  # untracked file is caught
    assert found["auth/session.py"].lines_added == 1
    assert found["auth/session.py"].lines_removed == 1
    assert found["tests/test_session.py"].funcs_removed == ["test_expired_session"]
    assert found["requirements.txt"].deps_added == ["redis"]
    assert found["utils/cache.py"].funcs_added == ["get"]


def test_private_file_is_never_read(login_repo):
    claude_edits(login_repo)
    env = by_path(find_changes(login_repo.root))[".env"]
    assert env.private and env.diff == ""
    output = format_groups(sort_into_groups(find_changes(login_repo.root)), "test")
    assert "abc123" not in output
    assert "private, contents not read" in output


def test_deleted_file(login_repo):
    login_repo.delete("tests/test_session.py")
    change = by_path(find_changes(login_repo.root))["tests/test_session.py"]
    assert change.kind == "deleted"
    assert change.funcs_removed == ["test_expired_session", "test_login"]


def test_staged_new_file_listed_once(login_repo):
    login_repo.write("new.py", "x = 1\n")
    login_repo.git("add", "new.py")
    found = find_changes(login_repo.root)
    assert [c.path for c in found] == ["new.py"]
    assert found[0].kind == "added"


def test_ignored_files_are_skipped(login_repo):
    login_repo.write(".gitignore", "node_modules/\n")
    login_repo.write("node_modules/lib/index.js", "x\n")
    assert [c.path for c in find_changes(login_repo.root)] == [".gitignore"]


def test_big_file_not_read(login_repo, monkeypatch):
    monkeypatch.setattr(changes, "BIG_FILE_BYTES", 10)
    login_repo.write("model.bin.txt", "x" * 50)
    change = find_changes(login_repo.root)[0]
    assert change.big and change.diff == ""


def test_binary_file_not_diffed(login_repo):
    (login_repo.root / "logo.png").write_bytes(b"\x89PNG\0\0data")
    change = find_changes(login_repo.root)[0]
    assert change.binary and change.diff == ""


def test_line_ending_only_change_is_ignored(login_repo):
    (login_repo.root / "docs/login.md").write_bytes(b"# Login\r\n")
    assert find_changes(login_repo.root) == []


def test_repo_with_no_commits(repo):
    repo.write("app.py", "def main():\n    pass\n")
    found = find_changes(repo.root)
    assert [(c.path, c.kind, c.funcs_added) for c in found] == [("app.py", "added", ["main"])]


def test_groups_for_the_example(login_repo):
    claude_edits(login_repo)
    login_repo.write("migrations/0002_sessions.sql", "ALTER TABLE users ADD ttl int;\n")
    groups = {title: [(name, what) for name, what, _ in items]
              for title, items in sort_into_groups(find_changes(login_repo.root)).items()}

    assert ("utils/cache.py", "new file") in groups["Additions"]
    assert ("redis", "new package (requirements.txt)") in groups["Additions"]
    assert ("get", "function added (utils/cache.py)") in groups["Additions"]
    assert ("test_expired_session", "test removed (tests/test_session.py)") in groups["Removals"]
    assert ("auth/session.py", "auth, +1 -1") in groups["Edits"]
    assert (".env", "new file, private, contents not read") in groups["Additions"]
    assert groups["Schema"] == [("migrations/0002_sessions.sql", "added")]


@pytest.mark.parametrize("path, kind", [
    (".env", "private"), ("config/prod.pem", "private"), (".env.example", "code"),
    ("tests/test_a.py", "test"), ("src/app.test.ts", "test"), ("pkg/server_test.go", "test"),
    ("db/migrations/001.py", "schema"), ("schema.prisma", "schema"),
    ("package-lock.json", "lockfile"), ("requirements-dev.txt", "dependencies"),
    (".github/workflows/ci.yml", "config"), ("Dockerfile", "config"), (".claude/settings.json", "config"),
    ("README.md", "docs"), ("auth/session.py", "auth"), ("billing/charge.py", "auth"),
    ("utils/cache.py", "code"),
])
def test_file_kind(path, kind):
    assert file_kind(path) == kind


def test_env_example_is_not_private():
    assert not is_private(".env.example")
    assert is_private(".env.production")


@pytest.mark.parametrize("path", [
    "auth/views.py", "auth_service.py", "src/AuthService.ts", "login.py", "LoginForm.tsx",
    "api/oauth2.py", "OAuthClient.java", "authentication.py", "payments/stripe.py", "billing.go",
    "checkout-page.js", "user_password_reset.py", "auth/session.py",
])
def test_auth_words_match(path):
    assert file_kind(path) == "auth"


@pytest.mark.parametrize("path", ["author.py", "authority.py", "tokenizer.py", "src/Authors.ts",
                                  "relogin_helper.py", "passwordless_ui_kit.py"])
def test_auth_inside_other_words_does_not_match(path):
    assert file_kind(path) == "code"

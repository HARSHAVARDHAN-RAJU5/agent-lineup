from lineup import git


def test_git_dir(repo):
    assert git.git_dir(repo.root).resolve() == (repo.root / ".git").resolve()


def test_files_between_commits(repo):
    repo.write("app.py", "x = 1\n")
    repo.write("docs/login.md", "# Login\n")
    repo.commit()
    start = git.head_commit(repo.root)
    repo.write("docs/login.md", "# Login\nNew timeout.\n")
    repo.write("app.py", "x = 2\n")  # changed but not committed
    repo.git("add", "docs/login.md")
    repo.git("commit", "-q", "-m", "docs")
    assert git.files_between(repo.root, start, git.head_commit(repo.root)) == ["docs/login.md"]


def test_ignored_files_lists_files_inside_hidden_folders(repo):
    repo.write(".gitignore", ".env\nnode_modules/\nconfig/\n")
    repo.write(".env", "SECRET_KEY=abc123\n")
    repo.write("config/.env", "SECRET_KEY=abc123\n")
    repo.write("node_modules/a/index.js", "x\n")
    repo.write("app.py", "x = 1\n")
    assert sorted(git.ignored_files(repo.root)) == [".env", "config/.env", "node_modules/a/index.js"]


def test_git_filter_and_is_private_use_the_same_list(repo):
    """git's pre-filter must find every file is_private() would flag, in any folder and any case."""
    from lineup.guard import is_private, private_pathspecs
    names = [".env", "config/.env", "a/b/.ENV", ".env.production", "prod.env", "keys/server.pem",
             "Certs/Server.PEM", "x/api.key", "id_rsa", "home/.ssh/id_ed25519.pub", "credentials.json",
             "my_secret.txt", ".npmrc", "deep/.netrc", ".env.example", "app.js", "node_modules/a/index.js"]
    repo.write(".gitignore", "*\n")
    for name in names:
        repo.write(name, "x\n")
    expected = {n for n in names if is_private(n)}
    from_git = set(git.ignored_files(repo.root, private_pathspecs()))
    assert expected <= from_git
    assert "app.js" not in from_git and "node_modules/a/index.js" not in from_git

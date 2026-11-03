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


def test_ignored_files_collapse_folders(repo):
    repo.write(".gitignore", ".env\nnode_modules/\n")
    repo.write(".env", "SECRET_KEY=abc123\n")
    repo.write("node_modules/a/index.js", "x\n")
    repo.write("node_modules/b/index.js", "x\n")
    repo.write("app.py", "x = 1\n")
    assert sorted(git.ignored_files(repo.root)) == [".env", "node_modules/"]

import subprocess
from pathlib import Path

import pytest


class Repo:
    """A throwaway git repo for tests."""

    def __init__(self, root: Path):
        self.root = root
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8", newline="")

    def delete(self, path):
        (self.root / path).unlink()

    def commit(self, message="commit"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


@pytest.fixture(autouse=True)
def no_real_agent(monkeypatch):
    """Tests never call a real AI: every agent looks "not installed" unless a test uses a fake one."""
    from lineup import llm
    monkeypatch.setattr(llm, "installed", lambda command: None)

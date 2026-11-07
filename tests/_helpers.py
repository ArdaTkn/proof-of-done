"""Helpers for building throwaway git repositories in tests."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "pod-tests",
    "GIT_AUTHOR_EMAIL": "tests@example.com",
    "GIT_COMMITTER_NAME": "pod-tests",
    "GIT_COMMITTER_EMAIL": "tests@example.com",
}


@dataclass
class Repo:
    """A temporary repository with convenience helpers."""

    root: Path
    _extra_env: dict[str, str] = field(default_factory=dict)

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env={**_base_env(), **GIT_ENV, **self._extra_env},
        )
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_bytes(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def commit(self, message: str = "update") -> str:
        self.git("add", "-A")
        self.git("commit", "-m", message, "--quiet")
        return self.git("rev-parse", "HEAD").strip()


def _base_env() -> dict[str, str]:
    return dict(os.environ)


def make_repo(root: Path, files: dict[str, str] | None = None, *, commit: bool = True) -> Repo:
    root.mkdir(parents=True, exist_ok=True)
    repo = Repo(root)
    repo.git("init", "--quiet", "-b", "main")
    repo.git("config", "user.email", "tests@example.com")
    repo.git("config", "user.name", "pod-tests")
    repo.git("config", "commit.gpgsign", "false")
    repo.git("config", "core.autocrlf", "false")
    for name, content in (files or {}).items():
        repo.write(name, content)
    if commit and files:
        repo.commit("init")
    return repo


PASSING_PROJECT: dict[str, str] = {
    "app/__init__.py": "",
    "app/core.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "tests/test_core.py": (
        "from app.core import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n"
    ),
}

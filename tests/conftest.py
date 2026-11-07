"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers import PASSING_PROJECT, Repo, make_repo


@pytest.fixture
def repo_factory(tmp_path: Path):
    """Build repositories with a callable: ``repo_factory(files, commit=True)``."""

    counter = {"n": 0}

    def _factory(files: dict[str, str] | None = None, *, commit: bool = True) -> Repo:
        counter["n"] += 1
        return make_repo(tmp_path / f"repo{counter['n']}", files, commit=commit)

    return _factory


@pytest.fixture
def clean_repo(repo_factory) -> Repo:
    """A repository whose tree is clean (nothing to verify)."""
    return repo_factory(PASSING_PROJECT)


@pytest.fixture
def dirty_repo(repo_factory) -> Repo:
    """A repository with one real change and a green suite."""
    repo = repo_factory(PASSING_PROJECT)
    repo.write("app/core.py", "def add(a: int, b: int) -> int:\n    return b + a\n")
    return repo

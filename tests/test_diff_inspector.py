"""Gate 1: the diff inspector must reject unverifiable, empty claims."""

from __future__ import annotations

from pathlib import Path

from proof_of_done.diff_inspector import inspect


def test_clean_tree_is_rejected(clean_repo) -> None:
    report = inspect(clean_repo.root)

    assert report.is_git_repo is True
    assert report.ok is False
    assert report.changes == ()
    assert "no changes detected" in report.summary()


def test_modified_file_reports_line_delta(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "a = 1\nb = 2\nc = 3\n"})
    repo.write("app/main.py", "a = 1\nb = 20\nc = 3\nd = 4\n")

    report = inspect(repo.root)

    assert report.ok is True
    assert report.added == 2
    assert report.removed == 1
    change = report.changes[0]
    assert change.path == "app/main.py"
    assert change.is_python is True
    assert report.python_files == ("app/main.py",)


def test_new_untracked_file_counts_as_change(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write("app/feature.py", "def feature() -> int:\n    return 42\n")

    report = inspect(repo.root)

    assert report.ok is True
    assert "app/feature.py" in report.python_files
    assert "app/feature.py" in report.untracked_files
    assert report.added >= 2


def test_untracked_directory_is_expanded(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write("pkg/nested/mod.py", "VALUE = 1\n")

    report = inspect(repo.root)

    assert report.ok is True
    assert "pkg/nested/mod.py" in [change.path for change in report.changes]


def test_staged_change_is_detected(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write("app/main.py", "x = 2\n")
    repo.git("add", "app/main.py")

    report = inspect(repo.root)

    assert report.ok is True
    assert report.added == 1 and report.removed == 1


def test_unborn_branch_with_staged_file_is_detected(repo_factory) -> None:
    repo = repo_factory(None, commit=False)
    repo.write("app/first.py", "print('hello')\n")
    repo.git("add", "app/first.py")

    report = inspect(repo.root)

    assert report.ok is True
    assert report.added == 1
    assert "app/first.py" in report.python_files


def test_deleted_file_is_counted_but_not_parsed(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n", "app/old.py": "y = 2\n"})
    (repo.root / "app/old.py").unlink()

    report = inspect(repo.root)

    assert report.ok is True
    assert report.removed == 1
    assert "app/old.py" in report.deleted_files
    assert "app/old.py" not in report.python_files


def test_base_revision_sees_committed_work(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write("app/main.py", "x = 2\ny = 3\n")
    repo.commit("change")

    assert inspect(repo.root).ok is False, "the tree is clean after committing"
    report = inspect(repo.root, base="HEAD~1")
    assert report.ok is True
    assert report.added >= 2


def test_unknown_base_revision_is_reported(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})

    report = inspect(repo.root, base="does-not-exist")

    assert report.ok is False
    assert "unknown base revision" in report.message


def test_non_git_directory_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "plain"
    target.mkdir()
    (target / "main.py").write_text("x = 1\n")

    report = inspect(target)

    assert report.is_git_repo is False
    assert report.ok is False
    assert "not a git repository" in report.message


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    report = inspect(tmp_path / "nope")

    assert report.ok is False
    assert "not a directory" in report.message


def test_binary_change_counts_as_concrete(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write_bytes("assets/logo.bin", b"\x00\x01\x02binary")

    report = inspect(repo.root)

    assert report.ok is True
    change = next(c for c in report.changes if c.path == "assets/logo.bin")
    assert change.binary is True
    assert "binary" in change.describe()


def test_tooling_artefacts_are_not_counted_as_work(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write_bytes("tests/__pycache__/test_main.cpython-314.pyc", b"\x00\x01")
    repo.write_bytes(".DS_Store", b"\x00\x01")
    repo.write("app/main.py", "x = 2\n")

    report = inspect(repo.root)

    assert [change.path for change in report.changes] == ["app/main.py"]
    assert report.added == 1 and report.removed == 1


def test_only_noise_means_no_verifiable_change(repo_factory) -> None:
    repo = repo_factory({"app/main.py": "x = 1\n"})
    repo.write_bytes("tests/__pycache__/noise.pyc", b"\x00")

    report = inspect(repo.root)

    assert report.ok is False, "pycache noise is not a change"
    assert report.changes == ()


def test_non_source_files_are_not_treated_as_source(repo_factory) -> None:
    repo = repo_factory({"README.md": "# hello\n"})
    repo.write("NOTES.txt", "notes\n")

    report = inspect(repo.root)

    assert report.ok is True
    assert "NOTES.txt" not in report.source_files
    assert report.python_files == ()

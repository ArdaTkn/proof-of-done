"""Gate 2: parsing and the anti-dummy assertion detector."""

from __future__ import annotations

from pathlib import Path

from proof_of_done.syntax_gate import check, check_file, is_test_path


def test_clean_file_passes(repo_factory) -> None:
    repo = repo_factory({"app/mod.py": "def f(x: int) -> int:\n    return x + 1\n"})

    report = check(["app/mod.py"], repo.root)

    assert report.ok is True
    assert report.checked == ("app/mod.py",)
    assert "parsed clean" in report.summary()


def test_syntax_error_reports_file_and_line(tmp_path: Path) -> None:
    target = tmp_path / "broken.py"
    target.write_text("def f(:\n    return 1\n")

    issues, count = check_file(target, tmp_path)

    assert count == 0
    assert len(issues) == 1
    assert issues[0].path == "broken.py"
    assert issues[0].line == 1
    assert issues[0].kind == "syntax"
    assert "syntax error" in issues[0].message


def test_unclosed_parenthesis_reports_last_line(repo_factory) -> None:
    repo = repo_factory({"app/broken.py": "def f(x):\n    return (x + 1\n"})

    report = check(["app/broken.py"], repo.root)

    assert report.ok is False
    assert len(report.syntax_issues) == 1
    issue = report.syntax_issues[0]
    assert issue.path == "app/broken.py"
    assert issue.line == 2
    assert "never closed" in issue.message


def test_indentation_error_is_caught(repo_factory) -> None:
    repo = repo_factory({"app/bad.py": "def f():\n    x = 1\n  y = 2\n"})

    report = check(["app/bad.py"], repo.root)

    assert report.ok is False
    assert report.syntax_issues[0].line == 3


def test_assert_true_is_flagged_as_hollow(repo_factory) -> None:
    repo = repo_factory({"tests/test_fake.py": "def test_ok():\n    assert True\n"})

    report = check(["tests/test_fake.py"], repo.root)

    assert report.ok is False
    assert len(report.dummy_issues) == 1
    assert report.dummy_issues[0].line == 2
    assert report.dummy_test_files == ("tests/test_fake.py",)


def test_constant_comparison_is_flagged(repo_factory) -> None:
    repo = repo_factory({"tests/test_fake.py": "def test_math():\n    assert 1 == 1\n"})

    report = check(["tests/test_fake.py"], repo.root)

    assert report.dummy_test_files == ("tests/test_fake.py",)


def test_self_comparison_is_flagged(repo_factory) -> None:
    repo = repo_factory(
        {"tests/test_fake.py": "def test_x():\n    value = 3\n    assert value == value\n"}
    )

    report = check(["tests/test_fake.py"], repo.root)

    assert len(report.dummy_issues) == 1


def test_not_false_is_flagged(repo_factory) -> None:
    repo = repo_factory({"tests/test_fake.py": "def test_x():\n    assert not False\n"})

    report = check(["tests/test_fake.py"], repo.root)

    assert len(report.dummy_issues) == 1


def test_real_assertions_are_not_flagged(repo_factory) -> None:
    source = (
        "def add(a, b):\n    return a + b\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n"
    )
    repo = repo_factory({"tests/test_real.py": source})

    report = check(["tests/test_real.py"], repo.root)

    assert report.ok is True
    assert report.dummy_test_files == ()


def test_mixed_assertions_are_not_a_dummy_file(repo_factory) -> None:
    source = "def test_x(value):\n    assert True\n    assert value > 0\n"
    repo = repo_factory({"tests/test_mixed.py": source})

    report = check(["tests/test_mixed.py"], repo.root)

    assert len(report.dummy_issues) == 1, "the hollow assert is still reported"
    assert report.dummy_test_files == (), "but the file is not wholesale fake"


def test_assertions_outside_tests_are_ignored(repo_factory) -> None:
    repo = repo_factory({"app/runtime.py": "VALUE = 1\nassert True\n"})

    report = check(["app/runtime.py"], repo.root)

    assert report.ok is True
    assert report.dummy_issues == ()


def test_conftest_counts_as_test_path() -> None:
    assert is_test_path("conftest.py") is True
    assert is_test_path("tests/conftest.py") is True
    assert is_test_path("app/service.py") is False
    assert is_test_path("src/thing_test.py") is True


def test_missing_file_is_skipped(repo_factory) -> None:
    repo = repo_factory({"app/mod.py": "x = 1\n"})

    report = check(["app/deleted.py"], repo.root)

    assert report.ok is True
    assert report.checked == ()
    assert report.skipped == ("app/deleted.py",)


def test_virtualenv_paths_are_skipped(repo_factory) -> None:
    repo = repo_factory({"app/mod.py": "x = 1\n"})
    repo.write(".venv/lib/site.py", "def broken(:\n")

    report = check([".venv/lib/site.py"], repo.root)

    assert report.ok is True
    assert report.skipped == (".venv/lib/site.py",)


def test_non_python_files_are_ignored(repo_factory) -> None:
    repo = repo_factory({"web/app.ts": "const x: number = 1\n"})

    report = check(["web/app.ts"], repo.root)

    assert report.ok is True
    assert report.checked == ()

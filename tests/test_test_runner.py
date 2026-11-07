"""Gate 3: the runner must report the real exit code and parse failures."""

from __future__ import annotations

import sys

import pytest

from proof_of_done.test_runner import TestReport, find_test_files, resolve_python, run_tests

FAILING_PROJECT = {
    "app/__init__.py": "",
    "app/auth.py": "def login(user: str) -> str:\n    return 'denied'\n",
    "tests/test_auth.py": (
        "from app.auth import login\n\n\n"
        "def test_login() -> None:\n"
        "    assert login('admin') == 'ok'\n"
    ),
}


def test_passing_suite_is_green(repo_factory) -> None:
    repo = repo_factory(
        {
            "app/__init__.py": "",
            "app/core.py": "def add(a, b):\n    return a + b\n",
            "tests/test_core.py": (
                "from app.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
                "def test_zero():\n    assert add(0, 0) == 0\n"
            ),
        }
    )

    report = run_tests(repo.root)

    assert report.ok is True
    assert report.ran is True
    assert report.framework == "pytest"
    assert report.passed == 2
    assert report.failed == 0
    assert report.exit_code == 0
    assert report.duration_s >= 0


def test_failing_test_is_parsed_with_file_and_line(repo_factory) -> None:
    repo = repo_factory(FAILING_PROJECT)

    report = run_tests(repo.root)

    assert report.ok is False
    assert report.failed == 1
    assert report.exit_code == 1
    failure = report.failures[0]
    assert failure.test_id.endswith("test_login")
    assert failure.file is not None and failure.file.endswith("tests/test_auth.py")
    assert failure.line == 5, "the assertion lives on line 5"
    assert failure.error_type == "AssertionError"
    assert "denied" in failure.message
    assert failure.location().endswith("test_auth.py:5")


def test_no_tests_fails_closed(repo_factory) -> None:
    repo = repo_factory({"app/core.py": "def add(a, b):\n    return a + b\n"})

    report = run_tests(repo.root)

    assert report.ran is False
    assert report.ok is False, "an unverified claim must not pass"
    assert "no tests found" in report.reason


def test_collection_error_is_a_failure(repo_factory) -> None:
    repo = repo_factory(
        {"tests/test_broken.py": "import does_not_exist\n\n\ndef test_x():\n    assert 1 == 1\n"}
    )

    report = run_tests(repo.root)

    assert report.ok is False
    assert report.exit_code != 0


def test_unittest_fallback_is_parsed(repo_factory) -> None:
    repo = repo_factory(
        {
            "tests/test_legacy.py": (
                "import unittest\n\n\n"
                "class Legacy(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(2 + 2, 4)\n\n"
                "    def test_bad(self):\n"
                "        self.assertEqual(2 + 2, 5)\n"
            )
        }
    )

    report = run_tests(repo.root, framework="unittest")

    assert report.framework == "unittest"
    assert report.ok is False
    assert report.failed == 1
    assert report.passed == 1
    assert report.failures[0].error_type == "AssertionError"


def test_timeout_is_reported(repo_factory) -> None:
    repo = repo_factory(
        {"tests/test_slow.py": ("import time\n\n\ndef test_slow():\n    time.sleep(5)\n")}
    )

    report = run_tests(repo.root, timeout_s=1)

    assert report.ran is True
    assert report.ok is False
    assert report.exit_code is None
    assert "timed out" in report.reason


def test_find_test_files_skips_virtualenvs(repo_factory) -> None:
    repo = repo_factory(
        {
            "tests/test_real.py": "def test_x():\n    assert 1 == 2\n",
            ".venv/lib/test_noise.py": "def test_n(): pass\n",
            "app/core.py": "x = 1\n",
        }
    )

    found = find_test_files(repo.root)

    assert "tests/test_real.py" in found
    assert all("venv" not in path for path in found)


def test_resolve_python_prefers_project_venv(repo_factory) -> None:
    repo = repo_factory({"app/core.py": "x = 1\n"})
    venv_python = repo.root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")

    assert resolve_python(repo.root) == str(venv_python)
    assert resolve_python(repo.root, override="/usr/bin/python3") == "/usr/bin/python3"
    assert resolve_python(repo.root) != "" and sys.executable


def test_report_summary_mentions_skipped(repo_factory) -> None:
    repo = repo_factory(
        {
            "tests/test_skip.py": (
                "import pytest\n\n\n"
                "@pytest.mark.skip(reason='later')\n"
                "def test_skipped():\n"
                "    assert False\n\n\n"
                "def test_ok():\n"
                "    assert True or False\n"
            )
        }
    )

    report = run_tests(repo.root)

    assert report.ok is True
    assert report.skipped == 1
    assert "skipped" in report.summary()


def test_blocked_report_defaults_to_not_ok() -> None:
    report = TestReport(ran=False, blocked=True, reason="not run: blocked by the changes gate")

    assert report.ok is False
    assert report.summary() == "not run: blocked by the changes gate"


@pytest.mark.parametrize("framework", ["pytest"])
def test_reported_command_uses_the_interpreter(repo_factory, framework: str) -> None:
    repo = repo_factory({"tests/test_ok.py": "def test_ok():\n    assert 1 < 2\n"})

    report = run_tests(repo.root, framework=framework)

    assert report.command[0] == sys.executable
    assert "-m" in report.command

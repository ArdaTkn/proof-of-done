"""Gate 4: the corrective prompt must be specific, short and deterministic."""

from __future__ import annotations

from pathlib import Path

from proof_of_done.diff_inspector import DiffReport, FileChange
from proof_of_done.feedback_generator import (
    HEADER,
    build_fix_prompt,
    build_one_liner,
)
from proof_of_done.syntax_gate import SyntaxIssue, SyntaxReport
from proof_of_done.test_runner import TestFailure, TestReport


def _passing_diff() -> DiffReport:
    return DiffReport(
        root=Path("/tmp/repo"),
        is_git_repo=True,
        changes=(FileChange("app/core.py", "M", 2, 1),),
    )


def _clean_syntax() -> SyntaxReport:
    return SyntaxReport(checked=("app/core.py",))


def _green_tests() -> TestReport:
    return TestReport(ran=True, framework="pytest", exit_code=0, passed=4, duration_s=0.4)


def test_no_prompt_when_every_gate_passes() -> None:
    prompt = build_fix_prompt(diff=_passing_diff(), syntax=_clean_syntax(), tests=_green_tests())

    assert prompt == ""
    assert build_one_liner(diff=_passing_diff(), syntax=_clean_syntax(), tests=_green_tests()) == ""


def test_failing_test_prompt_is_surgical() -> None:
    tests = TestReport(
        ran=True,
        framework="pytest",
        exit_code=1,
        passed=3,
        failed=1,
        failures=(
            TestFailure(
                test_id="tests/test_auth.py::test_login",
                message="AssertionError: assert 'denied' == 'ok'",
                file="tests/test_auth.py",
                line=45,
                error_type="AssertionError",
                traceback=(
                    "____ test_login ____\n"
                    ">       assert login('admin') == 'ok'\n"
                    "E       AssertionError: assert 'denied' == 'ok'"
                ),
            ),
        ),
    )

    prompt = build_fix_prompt(diff=_passing_diff(), syntax=_clean_syntax(), tests=tests)

    assert prompt.startswith(HEADER)
    assert "tests/test_auth.py:45" in prompt
    assert "test_login" in prompt
    assert "AssertionError" in prompt
    assert "pod verify" in prompt
    assert "Do not claim completion until `pod verify` exits 0." in prompt
    assert "changes" in prompt
    assert "syntax" in prompt


def test_empty_diff_prompt_explains_the_illusion() -> None:
    diff = DiffReport(root=Path("/tmp/repo"), is_git_repo=True, changes=())
    prompt = build_fix_prompt(diff=diff, syntax=SyntaxReport(), tests=TestReport(ran=False))

    assert "No verifiable change" in prompt
    assert "empty diff means the work is missing" in prompt


def test_non_git_repository_prompt_mentions_git() -> None:
    diff = DiffReport(root=Path("/tmp/x"), is_git_repo=False, message="not a git repository")

    prompt = build_fix_prompt(diff=diff, syntax=None, tests=None)

    assert "not a git repository" in prompt
    assert "Initialise git" in prompt


def test_syntax_prompt_includes_location_and_code() -> None:
    syntax = SyntaxReport(
        checked=("app/broken.py",),
        issues=(
            SyntaxIssue(
                path="app/broken.py",
                line=12,
                column=4,
                message="syntax error: '(' was never closed -> `return (x + 1`",
                kind="syntax",
            ),
        ),
    )

    prompt = build_fix_prompt(diff=_passing_diff(), syntax=syntax, tests=None)

    assert "app/broken.py:12" in prompt
    assert "never closed" in prompt
    assert "Fix the syntax errors first" in prompt


def test_hollow_test_file_is_called_out() -> None:
    syntax = SyntaxReport(
        checked=("tests/test_fake.py",),
        issues=(
            SyntaxIssue(
                path="tests/test_fake.py",
                line=2,
                column=4,
                message="hollow assertion `assert True` can never fail meaningfully",
                kind="dummy-assert",
            ),
        ),
        dummy_test_files=("tests/test_fake.py",),
    )

    prompt = build_fix_prompt(diff=_passing_diff(), syntax=syntax, tests=None)

    assert "tests/test_fake.py" in prompt
    assert "Hollow tests" in prompt


def test_missing_tests_ask_for_a_real_test() -> None:
    tests = TestReport(ran=False, reason="no tests found (expected test_*.py)")

    prompt = build_fix_prompt(diff=_passing_diff(), syntax=_clean_syntax(), tests=tests)

    assert "no tests found" in prompt
    assert "Add a test that fails when this behaviour breaks" in prompt


def test_blocked_tests_are_not_listed_as_failures() -> None:
    tests = TestReport(ran=False, blocked=True, reason="not run: blocked by the syntax gate")
    syntax = SyntaxReport(
        checked=("app/b.py",),
        issues=(SyntaxIssue("app/b.py", 1, 0, "syntax error: invalid syntax", "syntax"),),
    )

    prompt = build_fix_prompt(diff=_passing_diff(), syntax=syntax, tests=tests)

    assert "Failing tests" not in prompt
    assert "not run: blocked by the syntax gate" in prompt
    assert "[FAIL] syntax" in prompt


def test_prompt_is_deterministic_and_strips_ansi() -> None:
    tests = TestReport(
        ran=True,
        framework="pytest",
        exit_code=1,
        failed=1,
        failures=(
            TestFailure(
                test_id="tests/test_x.py::test_x",
                message="\x1b[31mboom\x1b[0m",
                file="tests/test_x.py",
                line=7,
                traceback="\x1b[31mE   ValueError: boom\x1b[0m",
            ),
        ),
    )

    first = build_fix_prompt(diff=_passing_diff(), syntax=_clean_syntax(), tests=tests)
    second = build_fix_prompt(diff=_passing_diff(), syntax=_clean_syntax(), tests=tests)

    assert first == second
    assert "\x1b[" not in first
    assert "ValueError: boom" in first


def test_one_liner_points_at_the_first_real_problem() -> None:
    syntax = SyntaxReport(
        checked=("app/b.py",),
        issues=(SyntaxIssue("app/b.py", 3, 0, "syntax error: invalid syntax", "syntax"),),
    )
    diff = DiffReport(root=Path("/tmp"), is_git_repo=True, changes=())

    line = build_one_liner(diff=diff, syntax=syntax, tests=None)

    assert line.startswith(HEADER)
    assert "app/b.py:3" in line
    assert "\n" not in line


def test_actions_only_mention_gates_that_failed() -> None:
    syntax = SyntaxReport(
        checked=("app/b.py",),
        issues=(SyntaxIssue("app/b.py", 1, 0, "syntax error: invalid syntax", "syntax"),),
    )
    prompt = build_fix_prompt(diff=_passing_diff(), syntax=syntax, tests=None)

    assert "do not rewrite passing code" not in prompt
    assert "Produce the actual change" not in prompt

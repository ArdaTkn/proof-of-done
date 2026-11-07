"""CLI behaviour: exit codes, JSON output and the prompt flag."""

from __future__ import annotations

import io
import json

from rich.console import Console

from proof_of_done.cli import EXIT_ERROR, EXIT_OK, EXIT_REJECTED, main, verify


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, force_terminal=False, color_system=None)


def test_clean_tree_exits_one(clean_repo) -> None:
    assert main(["check", str(clean_repo.root)], console=_console()) == EXIT_REJECTED


def test_verified_change_exits_zero(dirty_repo) -> None:
    console = _console()

    code = main(["check", str(dirty_repo.root)], console=console)

    assert code == EXIT_OK, console.file.getvalue()
    assert "VERIFIED" in console.file.getvalue()


def test_verify_alias_behaves_like_check(dirty_repo) -> None:
    assert main(["verify", str(dirty_repo.root)], console=_console()) == EXIT_OK


def test_no_subcommand_prints_help_and_exits_two(capsys) -> None:
    assert main([], console=_console()) == EXIT_ERROR
    assert "usage" in capsys.readouterr().out.lower()


def test_json_output_is_machine_readable(clean_repo, capsys) -> None:
    code = main(["check", str(clean_repo.root), "--json"], console=_console())

    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_REJECTED
    assert payload["ok"] is False
    assert set(payload["gates"]) == {"changes", "syntax", "tests"}
    assert payload["gates"]["changes"]["ok"] is False
    assert payload["fix_prompt"].startswith("PROOF-OF-DONE REJECTED")


def test_json_reports_green_run(dirty_repo, capsys) -> None:
    code = main(["check", str(dirty_repo.root), "--json"], console=_console())

    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["ok"] is True
    assert payload["gates"]["tests"]["passed"] == 1
    assert payload["gates"]["syntax"]["ok"] is True


def test_fix_prompt_flag_prints_the_prompt(clean_repo) -> None:
    console = _console()

    code = main(["check", str(clean_repo.root), "--fix-prompt"], console=console)

    output = console.file.getvalue()
    assert code == EXIT_REJECTED
    assert "fix prompt" in output
    assert "PROOF-OF-DONE REJECTED" in output


def test_quiet_mode_prints_nothing(clean_repo, capsys) -> None:
    code = main(["check", str(clean_repo.root), "--quiet"], console=_console())

    captured = capsys.readouterr()
    assert code == EXIT_REJECTED
    assert captured.out == ""


def test_missing_path_exits_rejected(tmp_path) -> None:
    assert main(["check", str(tmp_path / "nope")], console=_console()) == EXIT_REJECTED


def test_syntax_break_in_change_is_rejected(repo_factory) -> None:
    from _helpers import PASSING_PROJECT

    repo = repo_factory(PASSING_PROJECT)
    repo.write("app/core.py", "def add(a, b):\n    return a + b\n\n\ndef broken(:\n")
    console = _console()

    code = main(["check", str(repo.root)], console=console)

    assert code == EXIT_REJECTED
    assert "app/core.py" in console.file.getvalue()


def test_verify_object_short_circuits_tests_on_syntax_error(repo_factory) -> None:
    repo = repo_factory(
        {"app/x.py": "x = 1\n", "tests/test_x.py": "def test_x():\n    assert 1 == 2\n"}
    )
    repo.write("app/x.py", "def broken(:\n")

    result = verify(repo.root)

    assert result.ok is False
    assert result.tests.blocked is True
    assert result.tests.ran is False
    assert "syntax" in result.tests.reason

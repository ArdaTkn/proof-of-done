"""Terminal interface -- ``pod verify`` / ``pod check``.

Exit codes are part of the contract:

* ``0`` -- every gate passed; the completion claim is backed by evidence.
* ``1`` -- at least one gate rejected the claim.
* ``2`` -- the checker itself could not run (bad arguments, internal error).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .diff_inspector import DiffReport, inspect
from .feedback_generator import build_fix_prompt, build_one_liner
from .syntax_gate import SyntaxReport
from .syntax_gate import check as check_syntax
from .test_runner import TestReport, run_tests

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_ERROR = 2

_MAX_LISTED_FILES = 6


@dataclass(frozen=True)
class Verification:
    """The full outcome of one verification run."""

    diff: DiffReport
    syntax: SyntaxReport
    tests: TestReport
    prompt: str

    @property
    def ok(self) -> bool:
        return self.diff.ok and self.syntax.ok and self.tests.ok


def verify(
    path: Path | str = ".",
    *,
    base: str | None = None,
    python: str | None = None,
    timeout_s: int = 900,
) -> Verification:
    """Run every gate in order, short-circuiting tests on broken syntax."""
    diff = inspect(path, base=base)

    syntax = check_syntax(diff.python_files, diff.root) if diff.ok else SyntaxReport()

    if diff.ok and syntax.ok:
        tests = run_tests(diff.root, python=python, timeout_s=timeout_s)
    else:
        tests = TestReport(
            ran=False,
            blocked=True,
            reason=f"not run: blocked by the {'syntax' if diff.ok else 'changes'} gate",
        )

    prompt = build_fix_prompt(diff=diff, syntax=syntax, tests=tests)
    return Verification(diff=diff, syntax=syntax, tests=tests, prompt=prompt)


def _badge(passed: bool, *, skipped: bool = False) -> Text:
    if skipped:
        return Text(" SKIP ", style="bold black on yellow")
    if passed:
        return Text(" PASS ", style="bold black on green")
    return Text(" FAIL ", style="bold white on red")


def _file_lines(paths: Sequence[str], *, empty: str = "none") -> str:
    if not paths:
        return empty
    listed = list(paths[:_MAX_LISTED_FILES])
    suffix = f" (+{len(paths) - _MAX_LISTED_FILES} more)" if len(paths) > len(listed) else ""
    return "\n".join(f"  {escape(p)}" for p in listed) + escape(suffix)


def _gates_table(result: Verification) -> Table:
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim", expand=False)
    table.add_column("gate", style="bold", no_wrap=True)
    table.add_column("state", no_wrap=True)
    table.add_column("evidence", overflow="fold")

    diff_detail = Text(result.diff.summary())
    if result.diff.ok and result.diff.source_files:
        diff_detail.append("\n")
        diff_detail.append(_file_lines(result.diff.source_files), style="dim")
    table.add_row("changes", _badge(result.diff.ok), diff_detail)

    syntax_detail = Text(result.syntax.summary())
    for issue in result.syntax.issues[:3]:
        syntax_detail.append("\n")
        syntax_detail.append(f"{issue.location()} {issue.message}", style="dim")
    if result.syntax.dummy_test_files:
        syntax_detail.append("\n")
        syntax_detail.append(
            "hollow tests: " + ", ".join(result.syntax.dummy_test_files), style="dim"
        )
    syntax_skipped = not result.diff.ok
    table.add_row(
        "syntax",
        _badge(result.syntax.ok, skipped=syntax_skipped),
        Text("blocked by the change gate") if syntax_skipped else syntax_detail,
    )

    tests_skipped = result.tests.blocked
    tests_detail = Text(result.tests.summary())
    for failure in result.tests.failures[:3]:
        tests_detail.append("\n")
        tests_detail.append(failure.one_line(), style="dim")
    table.add_row(
        "tests",
        _badge(result.tests.ok, skipped=tests_skipped),
        Text(result.tests.reason) if tests_skipped else tests_detail,
    )
    return table


def render(console: Console, result: Verification, *, show_prompt: bool, copied: bool) -> None:
    """Print the human-facing report."""
    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("proof-of-done", "bold"),
                (f"  v{__version__}", "dim"),
                ("\nverification gate for agent completion claims", "dim"),
            ),
            border_style="bright_black",
            padding=(0, 2),
        )
    )
    console.print()
    console.print(_gates_table(result))
    console.print()

    if result.ok:
        console.print(
            Panel(
                Text.assemble(
                    ("VERIFIED", "bold green"),
                    ("  claim is backed by a diff, parsing code and a green suite", "green"),
                ),
                border_style="green",
                padding=(0, 1),
            )
        )
        return

    one_liner = build_one_liner(diff=result.diff, syntax=result.syntax, tests=result.tests)
    reason = one_liner.split(": ", 1)[-1] if one_liner else ""
    console.print(
        Panel(
            Text.assemble(("REJECTED", "bold white on red"), ("  " + reason, "red")),
            border_style="red",
            padding=(0, 1),
        )
    )

    if show_prompt and result.prompt:
        console.print()
        console.print(
            Panel(
                Text(result.prompt, style=""),
                title="fix prompt",
                subtitle="paste this back to the agent",
                subtitle_align="right",
                border_style="yellow",
                padding=(0, 1),
            )
        )
        if copied:
            console.print("[green]copied to clipboard[/green]")
        elif not result.ok:
            console.print("[dim]tip: add --copy to put the prompt on your clipboard[/dim]")


def _copy_to_clipboard(text: str) -> bool:
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        candidates.append(["pbcopy"])
    elif sys.platform.startswith("win"):
        candidates.append(["clip"])
    else:
        candidates.append(["wl-copy"])
        candidates.append(["xclip", "-selection", "clipboard"])
        candidates.append(["xsel", "--clipboard", "--input"])
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        try:
            proc = subprocess.run(command, input=text, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - env
            continue
        if proc.returncode == 0:
            return True
    return False


def _as_json(result: Verification) -> str:
    payload = {
        "ok": result.ok,
        "version": __version__,
        "gates": {
            "changes": {
                "ok": result.diff.ok,
                "is_git_repo": result.diff.is_git_repo,
                "base": result.diff.base,
                "summary": result.diff.summary(),
                "added": result.diff.added,
                "removed": result.diff.removed,
                "files": [
                    {
                        "path": change.path,
                        "status": change.status,
                        "added": change.added,
                        "removed": change.removed,
                        "binary": change.binary,
                    }
                    for change in result.diff.changes
                ],
            },
            "syntax": {
                "ok": result.syntax.ok,
                "summary": result.syntax.summary(),
                "checked": list(result.syntax.checked),
                "issues": [
                    {
                        "path": issue.path,
                        "line": issue.line,
                        "column": issue.column,
                        "kind": issue.kind,
                        "message": issue.message,
                    }
                    for issue in result.syntax.issues
                ],
                "dummy_test_files": list(result.syntax.dummy_test_files),
            },
            "tests": {
                "ok": result.tests.ok,
                "ran": result.tests.ran,
                "framework": result.tests.framework,
                "exit_code": result.tests.exit_code,
                "passed": result.tests.passed,
                "failed": result.tests.failed,
                "errors": result.tests.errors,
                "skipped": result.tests.skipped,
                "duration_s": result.tests.duration_s,
                "reason": result.tests.reason,
                "failures": [
                    {
                        "test_id": failure.test_id,
                        "file": failure.file,
                        "line": failure.line,
                        "error_type": failure.error_type,
                        "message": failure.message,
                    }
                    for failure in result.tests.failures
                ],
            },
        },
        "fix_prompt": result.prompt,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _add_verify_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    for name, help_text in (
        ("check", "verify the working tree and exit 0/1"),
        ("verify", "alias of `check`"),
    ):
        parser = subparsers.add_parser(name, help=help_text, description=help_text)
        parser.add_argument(
            "path",
            nargs="?",
            default=".",
            help="repository to verify (default: current directory)",
        )
        parser.add_argument(
            "--base", default=None, help="git revision to diff against (default: HEAD)"
        )
        parser.add_argument("--python", default=None, help="interpreter used to run the test suite")
        parser.add_argument(
            "--timeout",
            type=int,
            default=900,
            metavar="SECONDS",
            help="kill the test run after this many seconds (default: 900)",
        )
        parser.add_argument(
            "--fix-prompt",
            action="store_true",
            help="print the corrective prompt to paste back into the agent",
        )
        parser.add_argument(
            "--copy",
            action="store_true",
            help="copy the corrective prompt to the clipboard",
        )
        parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
        parser.add_argument("--quiet", action="store_true", help="no output, only the exit code")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pod",
        description="Deterministic verification gate for AI agent completion claims.",
        epilog="exit codes: 0 verified, 1 rejected, 2 checker error",
    )
    parser.add_argument("--version", action="version", version=f"proof-of-done {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="{check,verify}")
    _add_verify_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None, *, console: Console | None = None) -> int:
    """Entry point for tests and the console script. Returns the exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_ERROR

    console = console or Console(highlight=False)

    try:
        result = verify(
            args.path,
            base=args.base,
            python=args.python,
            timeout_s=args.timeout,
        )
    except Exception as exc:  # pragma: no cover - defensive
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        elif not args.quiet:
            console.print(f"[red]proof-of-done could not run:[/red] {escape(str(exc))}")
        return EXIT_ERROR

    if args.json:
        print(_as_json(result))
    elif not args.quiet:
        copied = False
        if result.prompt and args.copy:
            copied = _copy_to_clipboard(result.prompt)
        render(console, result, show_prompt=args.fix_prompt or args.copy, copied=copied)

    if (
        not result.ok
        and args.fix_prompt
        and not args.json
        and not args.quiet
        and not sys.stdout.isatty()
    ):
        # Piped output gets the bare prompt, so `pod check --fix-prompt | pbcopy` works.
        console.print(result.prompt, markup=False, highlight=False, soft_wrap=True)

    return EXIT_OK if result.ok else EXIT_REJECTED


def run() -> None:
    """Console-script wrapper: turns the return code into a process exit code."""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    run()

"""Gate 2 -- AST and syntax inspection.

Every changed Python file is parsed with :func:`ast.parse`. Files that cannot be
parsed are reported with the exact file, line and column of the breakage, which
is usually the difference between "I finished it" and "it imports".

The same pass runs the *anti-dummy* check: an ``assert`` whose condition is a
constant truthiness (``assert True``, ``assert 1 == 1``, ``assert x == x``) proves
nothing about the code under test. A test module whose asserts are *all* hollow
is flagged as a cheating test file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .diff_inspector import NOISE_PATH_PARTS

#: Directories that never contain project sources worth parsing.
SKIP_DIRS: Final[frozenset[str]] = NOISE_PATH_PARTS

_MAX_REPORTED_ISSUES: Final[int] = 50


@dataclass(frozen=True, slots=True)
class SyntaxIssue:
    """A single blocking finding for one file."""

    path: str
    line: int
    column: int
    message: str
    kind: str = "syntax"  # "syntax" | "dummy-assert"

    def location(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True, slots=True)
class SyntaxReport:
    """Outcome of the syntax + anti-dummy gate."""

    checked: tuple[str, ...] = ()
    issues: tuple[SyntaxIssue, ...] = ()
    dummy_test_files: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def syntax_issues(self) -> tuple[SyntaxIssue, ...]:
        return tuple(issue for issue in self.issues if issue.kind == "syntax")

    @property
    def dummy_issues(self) -> tuple[SyntaxIssue, ...]:
        return tuple(issue for issue in self.issues if issue.kind == "dummy-assert")

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        if not self.checked:
            return "no Python files to parse"
        parsed = len(self.checked)
        if self.ok:
            return f"{parsed} file{'s' if parsed != 1 else ''} parsed clean"
        return (
            f"{len(self.syntax_issues)} syntax error(s), {len(self.dummy_issues)} hollow assert(s)"
        )


def _is_trivial_assert(test: ast.expr) -> bool:
    """True when the assertion can never fail for a meaningful reason."""
    if isinstance(test, ast.Constant):
        # assert True / assert 1 / assert "x" -- always true, proves nothing.
        return bool(test.value)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = test.operand
        return isinstance(inner, ast.Constant) and not inner.value
    if isinstance(test, ast.Compare):
        if all(isinstance(cmp, ast.Constant) for cmp in [test.left, *test.comparators]):
            return True
        # assert x == x, assert value is value
        if test.comparators and len(test.comparators) == 1:
            left = ast.dump(test.left)
            return left == ast.dump(test.comparators[0])
    if isinstance(test, ast.BoolOp):
        return all(_is_trivial_assert(value) for value in test.values)
    return False


def _asserts_in(path: str, tree: ast.AST) -> list[SyntaxIssue]:
    issues: list[SyntaxIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and _is_trivial_assert(node.test):
            snippet = ast.unparse(node.test) if hasattr(ast, "unparse") else "assert ..."
            issues.append(
                SyntaxIssue(
                    path=path,
                    line=node.lineno,
                    column=node.col_offset,
                    message=f"hollow assertion `assert {snippet}` can never fail meaningfully",
                    kind="dummy-assert",
                )
            )
    return issues


def _count_asserts(tree: ast.AST) -> int:
    return sum(1 for node in ast.walk(tree) if isinstance(node, ast.Assert))


def is_test_path(path: str) -> bool:
    """Heuristic used to decide whether the anti-dummy rule applies."""
    file_name = Path(path).name.lower()
    parts = {part.lower() for part in Path(path).parts[:-1]}
    if file_name.startswith("test_") or file_name.endswith(("_test.py", "_tests.py")):
        return True
    if file_name in {"conftest.py"}:
        return True
    return bool(parts & {"tests", "test", "testing"})


def check_file(file_path: Path, relative_to: Path | None = None) -> tuple[list[SyntaxIssue], int]:
    """Parse one file. Returns ``(issues, assert_count)``."""
    display = str(file_path.relative_to(relative_to)) if relative_to else str(file_path)
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (
            [
                SyntaxIssue(
                    path=display,
                    line=1,
                    column=0,
                    message=f"cannot read file: {exc.strerror or exc}",
                    kind="syntax",
                )
            ],
            0,
        )

    try:
        tree = ast.parse(source, filename=display)
    except SyntaxError as exc:
        detail = exc.msg or "invalid syntax"
        text = (exc.text or "").strip()
        message = f"syntax error: {detail}"
        if text:
            message = f"{message} -> `{text}`"
        return (
            [
                SyntaxIssue(
                    path=display,
                    line=exc.lineno or 1,
                    column=(exc.offset or 1) - 1,
                    message=message,
                    kind="syntax",
                )
            ],
            0,
        )
    except (ValueError, MemoryError) as exc:  # pragma: no cover - pathological input
        return (
            [
                SyntaxIssue(
                    path=display,
                    line=1,
                    column=0,
                    message=f"could not parse file: {exc}",
                    kind="syntax",
                )
            ],
            0,
        )

    assert_count = _count_asserts(tree)
    issues = _asserts_in(display, tree) if is_test_path(display) else []
    return issues, assert_count


def check(files: tuple[str, ...] | list[str], root: Path | str = ".") -> SyntaxReport:
    """Parse every changed Python file and run the anti-dummy assert scan.

    Args:
        files: Paths relative to ``root`` (as produced by the diff inspector).
        root: Repository root used to resolve the paths.
    """
    resolved_root = Path(root).expanduser().resolve()
    checked: list[str] = []
    skipped: list[str] = []
    issues: list[SyntaxIssue] = []
    dummy_files: list[str] = []

    for relative in files:
        if Path(relative).suffix.lower() != ".py":
            continue
        if SKIP_DIRS & set(Path(relative).parts):
            skipped.append(relative)
            continue
        absolute = (resolved_root / relative).resolve()
        if not absolute.is_file():
            skipped.append(relative)
            continue
        checked.append(relative)
        file_issues, assert_count = check_file(absolute, resolved_root)
        issues.extend(file_issues)
        hollow = [issue for issue in file_issues if issue.kind == "dummy-assert"]
        if assert_count and len(hollow) == assert_count:
            dummy_files.append(relative)

    issues.sort(key=lambda issue: (issue.path, issue.line, issue.column))
    return SyntaxReport(
        checked=tuple(checked),
        issues=tuple(issues[:_MAX_REPORTED_ISSUES]),
        dummy_test_files=tuple(dummy_files),
        skipped=tuple(skipped),
    )

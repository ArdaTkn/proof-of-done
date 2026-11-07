"""Gate 4 -- surgical feedback for the agent.

When any gate fails, dumping a wall of logs is useless. This module turns the
structured reports into one short, actionable prompt: what failed, where, the
exact error, and the single instruction that ends the session correctly.

The output is deterministic -- no timestamps, no colour codes, no absolute
paths -- so it can be diffed, cached or pasted into an agent verbatim.
"""

from __future__ import annotations

import re
from typing import Final

from .diff_inspector import DiffReport
from .syntax_gate import SyntaxReport
from .test_runner import TestFailure, TestReport

HEADER: Final[str] = "PROOF-OF-DONE REJECTED"
_FENCE: Final[str] = "```"
_ANSI_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RULE_RE: Final[re.Pattern[str]] = re.compile(r"^[=_]{2,}\s*.*$")
_MAX_TB_LINES: Final[int] = 12


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _gate_line(label: str, passed: bool, detail: str) -> str:
    badge = "PASS" if passed else "FAIL"
    return f"  [{badge}] {label:<8} {detail}"


def _diff_lines(diff: DiffReport) -> list[str]:
    if not diff.is_git_repo:
        return [
            f"   {diff.message or 'the working directory is not a git repository'}",
            "   Without a diff there is nothing to verify. "
            "Initialise git or point --path at the repo.",
        ]
    return [
        f"   `git diff {diff.base}` reported nothing to verify: {diff.summary()}.",
        "   An empty diff means the work is missing, not done. "
        "Make the change, or say what is still open.",
    ]


def _syntax_lines(syntax: SyntaxReport) -> list[str]:
    lines: list[str] = []
    for issue in syntax.syntax_issues:
        lines.append(f"   {issue.location()} -- {issue.message}")
    if syntax.dummy_test_files:
        lines.append("   Hollow tests (their assertions can never fail):")
        lines.extend(f"     - {path}" for path in syntax.dummy_test_files)
        lines.append("   Replace them with assertions that would fail on broken code.")
    elif syntax.dummy_issues:
        for issue in syntax.dummy_issues:
            lines.append(f"   {issue.location()} -- {issue.message}")
    return lines


def _failure_message(failure: TestFailure) -> str:
    message = failure.message.strip()
    body = _strip_ansi(failure.traceback)
    error_lines = [line[2:].strip() for line in body.splitlines() if line.startswith("E ")]
    if error_lines:
        candidate = " / ".join(dict.fromkeys(error_lines[:2]))
        if len(candidate) > len(message):
            return candidate
    return message


def _clean_traceback(traceback: str) -> list[str]:
    lines = _strip_ansi(traceback).strip().splitlines()
    while lines and (_RULE_RE.match(lines[0]) or not lines[0].strip()):
        lines.pop(0)
    while lines and (_RULE_RE.match(lines[-1]) or not lines[-1].strip()):
        lines.pop()
    return lines[-_MAX_TB_LINES:]


def _tests_lines(tests: TestReport) -> list[str]:
    lines: list[str] = []
    if not tests.ran:
        lines.append(f"   {tests.reason or 'the suite did not run'}")
        if tests.test_files:
            lines.append(f"   Test modules exist but produced no result ({tests.summary()}).")
        return lines

    failing = tests.failed + tests.errors
    lines.append(f"   {failing} failing test(s) out of the suite ({tests.summary()}).")
    for failure in tests.failures:
        lines.append("")
        lines.append(f"   {failure.location()}")
        if failure.test_id and not failure.test_id.startswith("<"):
            lines.append(f"   test:  {failure.test_id}")
        message = _failure_message(failure)
        if message:
            lines.append(f"   error: {message}")
        body = _clean_traceback(failure.traceback)
        if body:
            lines.append(f"   {_FENCE}")
            lines.extend(f"   {line}" for line in body)
            lines.append(f"   {_FENCE}")
    return lines


def build_fix_prompt(
    *,
    diff: DiffReport | None = None,
    syntax: SyntaxReport | None = None,
    tests: TestReport | None = None,
) -> str:
    """Compose the corrective prompt for whichever gates rejected the claim."""
    diff_failed = diff is not None and not diff.ok
    syntax_failed = syntax is not None and not syntax.ok
    tests_failed = tests is not None and not tests.ok and not tests.blocked

    if not (diff_failed or syntax_failed or tests_failed):
        return ""

    blocks: list[str] = [f"{HEADER} -- your completion claim is not backed by evidence.", ""]
    blocks.append("Gate results:")
    if diff is not None:
        blocks.append(_gate_line("changes", diff.ok, diff.summary()))
    if syntax is not None:
        blocks.append(_gate_line("syntax", syntax.ok, syntax.summary()))
    if tests is not None:
        blocks.append(_gate_line("tests", tests.ok, tests.summary()))

    sections: list[tuple[str, list[str]]] = []
    if diff_failed and diff is not None:
        sections.append(("No verifiable change", _diff_lines(diff)))
    if syntax_failed and syntax is not None:
        sections.append(("Broken Python (the tree does not parse)", _syntax_lines(syntax)))
    if tests_failed and tests is not None:
        sections.append(("Failing tests", _tests_lines(tests)))

    blocks.extend(["", "Blocking evidence:"])
    for index, (title, body) in enumerate(sections, start=1):
        blocks.append("")
        blocks.append(f"{index}. {title}")
        if body:
            blocks.extend(body)

    blocks.extend(["", "Required action:"])
    if syntax_failed:
        blocks.append("- Fix the syntax errors first; the suite cannot even import the tree.")
    if tests_failed and tests is not None:
        if tests.ran:
            blocks.append(
                "- Fix exactly the failing assertions above; do not rewrite passing code."
            )
        else:
            blocks.append("- Add a test that fails when this behaviour breaks, then make it pass.")
    if diff_failed:
        blocks.append("- Produce the actual change, or state plainly that the task is not done.")
    blocks.append("- Re-run `pod verify` and read its output.")
    blocks.append("- Do not claim completion until `pod verify` exits 0.")

    return "\n".join(blocks).strip() + "\n"


def build_one_liner(
    *,
    diff: DiffReport | None = None,
    syntax: SyntaxReport | None = None,
    tests: TestReport | None = None,
) -> str:
    """Compact single-line rejection, handy for CI logs and commit hooks."""
    reasons: list[str] = []
    if diff is not None and not diff.ok:
        reasons.append(f"no verifiable change ({diff.summary()})")
    if syntax is not None and not syntax.ok:
        first = syntax.syntax_issues[0] if syntax.syntax_issues else None
        if first is not None:
            reasons.append(f"{first.location()} {first.message}")
        elif syntax.dummy_test_files:
            reasons.append(f"hollow tests in {syntax.dummy_test_files[0]}")
    if tests is not None and not tests.ok:
        first_test = tests.failures[0] if tests.failures else None
        if first_test is not None:
            reasons.append(f"{first_test.location()} {first_test.message}")
        else:
            reasons.append(tests.summary())
    if not reasons:
        return ""
    return f"{HEADER}: " + "; ".join(reasons)

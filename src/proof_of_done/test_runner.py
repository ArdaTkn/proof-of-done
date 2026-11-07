"""Gate 3 -- deterministic test execution.

Runs the project's own suite headless, honours the exit code, and turns the
raw output into structured failures (file, line, error type, message) so the
feedback generator can be surgical instead of vague.

pytest is preferred when it is importable, because it also collects
``unittest.TestCase`` classes. ``unittest`` is the fallback.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .syntax_gate import SKIP_DIRS

PYTEST_QUIET_ARGS: Final[tuple[str, ...]] = (
    "-q",
    "--tb=short",
    "--color=no",
    "-p",
    "no:cacheprovider",
    "-o",
    "addopts=",
)
DEFAULT_TIMEOUT_S: Final[int] = 900
_OUTPUT_LIMIT: Final[int] = 6000

_COUNT_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|deselected)"
)
_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"in\s+([\d.]+)s")
_PYTEST_LOCATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<file>[^\s:]+\.py):(?P<line>\d+):\s*(?P<rest>.*)$"
)
_UNITTEST_LOCATION_RE: Final[re.Pattern[str]] = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)'
)
_TRACEBACK_HEADER_RE: Final[re.Pattern[str]] = re.compile(r"^_{2,}\s*(?P<name>.+?)\s*_{2,}$")


@dataclass(frozen=True, slots=True)
class TestFailure:
    """One failing test, already parsed into actionable pieces."""

    __test__ = False  # not a pytest test class

    test_id: str
    message: str
    file: str | None = None
    line: int | None = None
    error_type: str | None = None
    traceback: str = ""

    def location(self) -> str:
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        return self.test_id

    def one_line(self) -> str:
        head = self.location()
        if self.error_type and self.error_type not in self.message:
            return f"{head} -- {self.error_type}: {self.message}"
        return f"{head} -- {self.message}"


@dataclass(frozen=True, slots=True)
class TestReport:
    """Outcome of the test gate."""

    __test__ = False  # not a pytest test class

    ran: bool
    framework: str | None = None
    exit_code: int | None = None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failures: tuple[TestFailure, ...] = ()
    command: tuple[str, ...] = ()
    output: str = ""
    reason: str = ""
    test_files: tuple[str, ...] = field(default=())
    blocked: bool = False

    @property
    def ok(self) -> bool:
        if not self.ran:
            return False
        if self.exit_code != 0:
            return False
        return self.failed == 0 and self.errors == 0

    def summary(self) -> str:
        if not self.ran:
            return self.reason or "tests were not run"
        if self.ok:
            bits = [f"{self.passed} passed"]
            if self.skipped:
                bits.append(f"{self.skipped} skipped")
            return f"{', '.join(bits)} in {self.duration_s:.2f}s"
        bits = []
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.errors:
            bits.append(f"{self.errors} error(s)")
        if self.passed:
            bits.append(f"{self.passed} passed")
        if not bits:
            bits.append(f"exit code {self.exit_code}")
        return ", ".join(bits)


def find_test_files(root: Path) -> tuple[str, ...]:
    """Return test module paths (relative to ``root``) using a name heuristic."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if SKIP_DIRS & set(relative.parts):
            continue
        name = path.name.lower()
        in_test_dir = bool({p.lower() for p in relative.parts[:-1]} & {"tests", "test", "testing"})
        if (
            name.startswith("test_")
            or name.endswith(("_test.py", "_tests.py"))
            or (in_test_dir and name.endswith(".py"))
        ):
            found.append(str(relative))
    return tuple(found)


def resolve_python(root: Path, override: str | None = None) -> str:
    """Pick the interpreter that should run the target project's tests."""
    if override:
        return override
    for candidate in (
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _has_pytest(python: str, root: Path) -> bool:
    try:
        proc = subprocess.run(
            [python, "-c", "import pytest"],
            cwd=str(root),
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env
        return False
    return proc.returncode == 0


def _detect_framework(root: Path, test_files: tuple[str, ...], python: str) -> str | None:
    if not test_files:
        return None
    return "pytest" if _has_pytest(python, root) else "unittest"


def _counts_from_output(output: str) -> tuple[int, int, int, int]:
    passed = failed = errors = skipped = 0
    for raw_count, label in _COUNT_RE.findall(output):
        count = int(raw_count)
        if label == "passed":
            passed = count
        elif label == "failed":
            failed = count
        elif label.startswith("error"):
            errors = count
        elif label == "skipped":
            skipped = count
    return passed, failed, errors, skipped


def _split_tb_blocks(output: str) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in output.splitlines():
        if _TRACEBACK_HEADER_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current is not None:
            if line.startswith("=") and "short test summary" in line:
                blocks.append(current)
                current = None
            else:
                current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).strip() for block in blocks]


def _block_name(block: str) -> str:
    header = block.splitlines()[0] if block else ""
    match = _TRACEBACK_HEADER_RE.match(header)
    return match.group("name") if match else ""


def _parse_pytest(output: str) -> tuple[tuple[TestFailure, ...], tuple[int, int, int, int]]:
    passed, failed, errors, skipped = _counts_from_output(output)

    blocks = _split_tb_blocks(output)
    by_name: dict[str, str] = {}
    for block in blocks:
        by_name.setdefault(_block_name(block), block)

    failures: list[TestFailure] = []
    seen: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("FAILED ") or stripped.startswith("ERROR ")):
            continue
        payload = stripped.split(" ", 1)[1]
        test_id, _, message = payload.partition(" - ")
        test_id = test_id.strip()
        if test_id in seen:
            continue
        seen.add(test_id)

        error_type: str | None = None
        if ":" in message:
            candidate = message.split(":", 1)[0].strip()
            if candidate and " " not in candidate:
                error_type = candidate

        block = by_name.get(test_id.rsplit("::", 1)[-1], "")
        file_name: str | None = None
        line_number: int | None = None
        error_lines: list[str] = []
        for tb_line in block.splitlines():
            match = _PYTEST_LOCATION_RE.match(tb_line.strip())
            if match and not match.group("file").startswith("<"):
                file_name = match.group("file")
                line_number = int(match.group("line"))
            if tb_line.startswith("E "):
                error_lines.append(tb_line[2:].strip())

        if error_type is None and error_lines:
            head = error_lines[-1]
            if ":" in head:
                error_type = head.split(":", 1)[0].strip()

        failures.append(
            TestFailure(
                test_id=test_id,
                message=message.strip() or (error_lines[-1] if error_lines else "test failed"),
                file=file_name,
                line=line_number,
                error_type=error_type,
                traceback=block,
            )
        )

    # A hard crash (collection error, segfault) never emits a FAILED line.
    if not failures and (failed or errors or not passed):
        failures.append(
            TestFailure(
                test_id="<suite>",
                message="pytest could not complete the run",
                traceback=output[-_OUTPUT_LIMIT:],
            )
        )
    return tuple(failures), (passed, failed, errors, skipped)


def _parse_unittest(output: str) -> tuple[tuple[TestFailure, ...], tuple[int, int, int, int]]:
    lines = output.splitlines()
    failures: list[TestFailure] = []
    failed = errors = 0

    for index, line in enumerate(lines):
        if line.startswith(("FAIL: ", "ERROR: ")):
            kind = "error" if line.startswith("ERROR:") else "fail"
            if kind == "error":
                errors += 1
            else:
                failed += 1
            header = line.split(" ", 1)[1].strip()
            test_id = header.split(" (")[0].strip() or header
            file_name = line_number = None
            error_type = message = ""
            for probe in lines[index : index + 40]:
                match = _UNITTEST_LOCATION_RE.match(probe)
                if match:
                    file_name = match.group("file")
                    line_number = int(match.group("line"))
                if probe.startswith(("=", "---")) and probe is not line:
                    continue
                if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Warning)\b.*:", probe.strip()):
                    raw = probe.strip()
                    error_type, _, message = raw.partition(":")
                    message = message.strip()
                    break
            failures.append(
                TestFailure(
                    test_id=test_id,
                    message=message or "test failed",
                    file=file_name,
                    line=line_number,
                    error_type=error_type or None,
                )
            )

    passed = 0
    ran_match = re.search(r"^Ran (\d+) tests? in", output, re.MULTILINE)
    total = int(ran_match.group(1)) if ran_match else 0
    skipped_match = re.search(r"skipped=(\d+)", output)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    passed = max(total - failed - errors - skipped, 0)
    if not failures and (failed or errors):
        failures.append(
            TestFailure(
                test_id="<suite>", message="unittest reported failures", traceback=output[-2000:]
            )
        )
    return tuple(failures), (passed, failed, errors, skipped)


def run_tests(
    root: Path | str = ".",
    *,
    python: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    framework: str | None = None,
    extra_args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> TestReport:
    """Run the project suite and return a structured report.

    A repository with no discoverable tests is *not* a pass: the gate fails
    closed, because an unverified claim is indistinguishable from a false one.
    """
    resolved = Path(root).expanduser().resolve()
    interpreter = resolve_python(resolved, python)
    test_files = find_test_files(resolved)
    selected = framework or _detect_framework(resolved, test_files, interpreter)

    if selected is None:
        return TestReport(
            ran=False,
            framework=None,
            reason="no tests found (expected test_*.py / *_test.py, or a tests/ directory)",
            test_files=(),
        )

    if selected == "pytest":
        command = (interpreter, "-m", "pytest", *PYTEST_QUIET_ARGS, *extra_args)
    else:
        # Naming the files avoids unittest discovery's package requirements.
        command = (interpreter, "-m", "unittest", "-v", *test_files, *extra_args)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(resolved),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            env={**os.environ, **(env or {})},
        )
    except subprocess.TimeoutExpired:
        return TestReport(
            ran=True,
            framework=selected,
            exit_code=None,
            command=command,
            reason=f"test run timed out after {timeout_s}s",
            duration_s=round(time.monotonic() - started, 3),
            test_files=test_files,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
        return TestReport(
            ran=False,
            framework=selected,
            command=command,
            reason=f"could not start the test runner: {exc}",
            test_files=test_files,
        )

    elapsed = round(time.monotonic() - started, 3)
    output = (proc.stdout or "") + (proc.stderr or "")
    truncated = output if len(output) <= _OUTPUT_LIMIT else output[-_OUTPUT_LIMIT:]

    parser = _parse_pytest if selected == "pytest" else _parse_unittest
    failures, (passed, failed, errors, skipped) = parser(output)
    duration_match = _DURATION_RE.search(output)
    duration = float(duration_match.group(1)) if duration_match else elapsed

    reason = ""
    ran = True
    if selected == "pytest" and proc.returncode == 5:
        ran = False
        reason = "no tests collected by pytest"

    return TestReport(
        ran=ran,
        framework=selected,
        exit_code=proc.returncode,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration_s=duration,
        failures=failures if ran else (),
        command=command,
        output=truncated,
        reason=reason,
        test_files=test_files,
    )

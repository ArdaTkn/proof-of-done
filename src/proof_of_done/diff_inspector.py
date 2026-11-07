"""Gate 1 -- Git and change inspection.

Reads ``git status`` and ``git diff`` for the target working tree and answers a
single question: *did anything concrete change?* An agent that closes a session
with a clean tree has produced no evidence, so the claim is rejected.

The inspector never mutates the repository: it only runs read-only git
commands (``rev-parse``, ``status``, ``diff``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Extensions considered "real source" when summarising the work.
SOURCE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".rb",
        ".php",
        ".cs",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".m",
        ".mm",
        ".swift",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".ex",
        ".exs",
        ".dart",
        ".lua",
    }
)
PYTHON_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".pyi"})

#: Directories that hold build/tooling artefacts rather than real work.
NOISE_PATH_PARTS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "build",
        "dist",
        ".eggs",
        ".idea",
        ".vscode",
        "site-packages",
    }
)
#: Files that are never part of a human-visible change.
NOISE_FILE_NAMES: Final[frozenset[str]] = frozenset({".DS_Store", "Thumbs.db"})
NOISE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".pyc", ".pyo", ".so", ".dylib", ".dll", ".o", ".class", ".log", ".swp"}
)


def is_noise(path: str) -> bool:
    """True for artefacts a verification run should never mistake for work."""
    parts = Path(path).parts
    if NOISE_PATH_PARTS & set(parts):
        return True
    if parts and parts[-1] in NOISE_FILE_NAMES:
        return True
    return Path(path).suffix.lower() in NOISE_SUFFIXES


#: Porcelain status codes that mean "the file is gone" (nothing left to parse).
_DELETED_CODES: Final[frozenset[str]] = frozenset({"D"})

_BINARY_SNIFF_BYTES: Final[int] = 8192
_GIT_TIMEOUT_S: Final[int] = 60


class GitError(RuntimeError):
    """Raised when git is unavailable or refuses to run in the given directory."""


@dataclass(frozen=True, slots=True)
class FileChange:
    """A single touched path, with the line delta git reported for it."""

    path: str
    status: str
    added: int
    removed: int
    binary: bool = False

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def is_python(self) -> bool:
        return self.suffix in PYTHON_SUFFIXES

    @property
    def is_source(self) -> bool:
        return self.suffix in SOURCE_SUFFIXES

    @property
    def is_deleted(self) -> bool:
        return self.status in _DELETED_CODES

    @property
    def lines_changed(self) -> int:
        return self.added + self.removed

    def describe(self) -> str:
        if self.binary:
            return f"{self.status} {self.path} (binary)"
        return f"{self.status} {self.path} (+{self.added}/-{self.removed})"


@dataclass(frozen=True, slots=True)
class DiffReport:
    """Outcome of the change-inspection gate."""

    root: Path
    is_git_repo: bool
    changes: tuple[FileChange, ...] = ()
    message: str = ""
    base: str = "HEAD"

    @property
    def added(self) -> int:
        return sum(change.added for change in self.changes)

    @property
    def removed(self) -> int:
        return sum(change.removed for change in self.changes)

    @property
    def ok(self) -> bool:
        """True only when git reported a concrete, non-empty change."""
        if not self.is_git_repo or not self.changes:
            return False
        if any(change.binary for change in self.changes):
            return True
        return self.added + self.removed > 0

    @property
    def source_files(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.is_source and not c.is_deleted)

    @property
    def python_files(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.is_python and not c.is_deleted)

    @property
    def deleted_files(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.is_deleted)

    @property
    def untracked_files(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.status == "??")

    def summary(self) -> str:
        if not self.is_git_repo:
            return self.message or "not a git repository"
        if not self.changes:
            return self.message or f"no changes detected against {self.base}"
        files = len(self.changes)
        return (
            f"{files} file{'s' if files != 1 else ''} changed, +{self.added}/-{self.removed} lines"
        )


def _git(root: Path, *args: str) -> str:
    """Run a read-only git command and return stdout, or raise :class:`GitError`."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
        raise GitError(str(exc)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise GitError(detail[0] if detail else f"git {args[0]} failed")
    return proc.stdout


def _has_head(root: Path) -> bool:
    try:
        _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    except GitError:
        return False
    return True


def _parse_status(root: Path, base: str | None) -> dict[str, str]:
    """Return ``{path: porcelain_code}`` for the tree, without renames."""
    if base:
        raw = _git(root, "diff", "--name-status", "-z", "--no-renames", base, "--")
        tokens = [t for t in raw.split("\0") if t]
        return {tokens[i + 1]: tokens[i][:1] for i in range(0, len(tokens) - 1, 2)}

    raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    tokens = [t for t in raw.split("\0") if t]
    status: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        code, path = entry[:2], entry[3:]
        index += 1
        if "R" in code or "C" in code:
            # With -z a rename emits the destination first, then the source.
            index += 1
        status[path] = "??" if code == "??" else code.strip() or "M"
    return status


def _parse_numstat(root: Path, base: str | None) -> tuple[dict[str, tuple[int, int]], set[str]]:
    """Return ``({path: (added, removed)}, binary_paths)`` for tracked changes."""
    counts: dict[str, tuple[int, int]] = {}
    binaries: set[str] = set()

    def absorb(args: tuple[str, ...]) -> None:
        raw = _git(root, "diff", "--numstat", "-z", "--no-renames", *args, "--")
        for token in (t for t in raw.split("\0") if t):
            parts = token.split("\t", 2)
            if len(parts) != 3:
                continue
            added_raw, removed_raw, path = parts
            if added_raw == "-" or removed_raw == "-":
                binaries.add(path)
                counts[path] = (0, 0)
                continue
            counts[path] = (int(added_raw), int(removed_raw))

    if base:
        absorb((base,))
    elif _has_head(root):
        absorb(("HEAD",))
    else:
        # Unborn branch: staged files vs. the index, plus unstaged edits.
        absorb(("--cached",))
        absorb(())

    return counts, binaries


def _count_untracked(file_path: Path) -> tuple[int, bool]:
    """Count the lines an untracked file adds; flag binaries as touched."""
    try:
        data = file_path.read_bytes()
    except OSError:
        return 0, False
    if b"\0" in data[:_BINARY_SNIFF_BYTES]:
        return 0, True
    if not data:
        return 0, False
    text = data.decode("utf-8", errors="replace")
    return text.count("\n") + (0 if text.endswith("\n") else 1), False


def inspect(root: Path | str = ".", *, base: str | None = None) -> DiffReport:
    """Inspect ``root`` and report whether a concrete change exists.

    Args:
        root: Directory to inspect. Defaults to the current working directory.
        base: Git revision to diff against instead of the default ``HEAD``.

    Returns:
        A :class:`DiffReport`; ``report.ok`` is False for a clean tree, a
        missing repository or a repository without any touched file.
    """
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        return DiffReport(resolved, False, (), f"not a directory: {resolved}", base or "HEAD")

    try:
        inside = _git(resolved, "rev-parse", "--is-inside-work-tree").strip()
    except GitError as exc:
        return DiffReport(resolved, False, (), f"not a git repository ({exc})", base or "HEAD")

    if inside != "true":  # pragma: no cover - bare repository edge case
        return DiffReport(resolved, False, (), "not a git work tree", base or "HEAD")

    if base:
        try:
            # `^{tree}` accepts commits, tags and raw tree objects alike.
            _git(resolved, "rev-parse", "--verify", "--quiet", f"{base}^{{tree}}")
        except GitError:
            message = f"unknown base revision: {base}"
            return DiffReport(resolved, True, (), message, base or "HEAD")

    try:
        status = _parse_status(resolved, base)
        counts, binaries = _parse_numstat(resolved, base)
    except GitError as exc:
        return DiffReport(resolved, True, (), f"git inspection failed: {exc}", base or "HEAD")

    changes: list[FileChange] = []
    for path, code in status.items():
        if is_noise(path):
            continue
        added, removed = counts.get(path, (0, 0))
        binary = path in binaries
        if code == "??" and path not in counts:
            added, binary = _count_untracked(resolved / path)
        changes.append(
            FileChange(path=path, status=code, added=added, removed=removed, binary=binary)
        )

    # A --base comparison can surface paths that status (worktree-based) missed.
    for path, (added, removed) in counts.items():
        if path not in status and not is_noise(path):
            changes.append(
                FileChange(
                    path=path, status="M", added=added, removed=removed, binary=path in binaries
                )
            )

    changes.sort(key=lambda change: change.path)
    return DiffReport(
        root=resolved,
        is_git_repo=True,
        changes=tuple(changes),
        message="",
        base=base or "HEAD",
    )

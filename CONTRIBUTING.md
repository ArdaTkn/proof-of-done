# Contributing

Small tool, strict rules: every claim in a PR has to be reproducible by someone else.

## Setup

```bash
git clone https://github.com/ArdaTkn/proof-of-done && cd proof-of-done
uv venv && uv pip install -e ".[dev]"
```

## Before you open a PR

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pod check --base origin/main   # the tool auditing its own change
```

CI runs the same three commands on Linux (3.10 → 3.14) and macOS.

## What gets merged

- A behavioural change comes with a test that fails without it.
- New heuristics (test discovery, trivial-assert detection, noise filtering) must be
  conservative: a false rejection is worse than a missed one, because a gate people stop
  trusting is a gate people disable.
- Output stays deterministic: no timestamps, no absolute paths, no ANSI in `fix_prompt`.

## Adding a gate

A gate is a module that returns a frozen dataclass with an `ok` property and a `summary()`
method. Wire it into `cli.verify()` and give `feedback_generator` a section for it — a gate that
cannot explain itself is just a red exit code.

## Reporting a false positive

Open an issue with the exact `pod verify --json` output plus a minimal repo layout. Determinism
means every false positive is reproducible, which makes it fixable.

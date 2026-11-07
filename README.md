# proof-of-done

**Your agent said "done". This tool checks.**

[![CI](https://github.com/ArdaTkn/proof-of-done/actions/workflows/ci.yml/badge.svg)](https://github.com/ArdaTkn/proof-of-done/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![code style: ruff](https://img.shields.io/badge/style-ruff-261230)](https://github.com/astral-sh/ruff)

Agents do not lie on purpose. They summarise optimistically. They edit three files, forget the
fourth, never run the suite, and close the session with *"everything works"*. The failure is not
malice, it is an **unverified claim** — and it is verifiable.

`pod` (`proof-of-done`) is a small, deterministic gate that runs *after* the agent stops talking
and *before* you trust it. No model in the loop, no heuristics about intent: git, `ast`, and the
project's own exit codes. Four gates, one exit code, one surgical prompt to send back.

```console
$ pod verify
╭─────────────────────────────────────────────────╮
│  proof-of-done  v0.1.0                          │
│  verification gate for agent completion claims  │
╰─────────────────────────────────────────────────╯

gate     state   evidence
changes   PASS   1 file changed, +4/-0 lines
                   app/auth.py
syntax    PASS   1 file parsed clean
tests     PASS   2 passed in 0.00s

╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ VERIFIED  claim is backed by a diff, parsing code and a green suite                      │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

## The illusion it kills

An agent's report is a natural-language claim. A repository is a source of truth. `pod` compares
the two:

| Claim | Reality check | Verdict |
| --- | --- | --- |
| "Implemented the feature" | `git diff` shows 0 lines changed | rejected |
| "Code is clean" | `ast.parse` fails on `app/auth.py:42` | rejected |
| "Tests are green" | pytest exits `1`, `test_auth.py:45` asserts | rejected |
| "Added tests" | the only asserts are `assert True` / `assert 1 == 1` | rejected |

Nothing here needs a language model. Every verdict is reproducible on a fresh checkout, which is
also why it works in CI and git hooks.

## Demo: the agent's claim, audited

The agent rewrote `login()` to return a dict, left the tests untouched, and reported success.

```console
$ pod verify --fix-prompt
╭─────────────────────────────────────────────────╮
│  proof-of-done  v0.1.0                          │
│  verification gate for agent completion claims  │
╰─────────────────────────────────────────────────╯

gate     state   evidence
changes   PASS   2 files changed, +6/-4 lines
                   app/auth.py
                   tests/test_auth.py
syntax    PASS   2 files parsed clean
tests     FAIL   1 failed, 1 passed
                 tests/test_auth.py:5 -- AssertionError: assert {'ok': True} is...

╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ REJECTED  tests/test_auth.py:5 AssertionError: assert {'ok': True} is...                 │
╰──────────────────────────────────────────────────────────────────────────────────────────╯

╭─────────────────────────────────────── fix prompt ───────────────────────────────────────╮
│ PROOF-OF-DONE REJECTED -- your completion claim is not backed by evidence.               │
│                                                                                          │
│ Gate results:                                                                            │
│   [PASS] changes  2 files changed, +6/-4 lines                                           │
│   [PASS] syntax   2 files parsed clean                                                   │
│   [FAIL] tests    1 failed, 1 passed                                                     │
│                                                                                          │
│ Blocking evidence:                                                                       │
│                                                                                          │
│ 1. Failing tests                                                                         │
│    1 failing test(s) out of the suite (1 failed, 1 passed).                              │
│                                                                                          │
│    tests/test_auth.py:5                                                                  │
│    test:  tests/test_auth.py::test_admin_can_log_in                                      │
│    error: AssertionError: assert {'ok': True} is True / +  where {'ok': True} =          │
│ login('admin', 'secret')                                                                 │
│    ```                                                                                   │
│    tests/test_auth.py:5: in test_admin_can_log_in                                        │
│        assert login("admin", "secret") is True                                           │
│    E   AssertionError: assert {'ok': True} is True                                       │
│    E    +  where {'ok': True} = login('admin', 'secret')                                 │
│    ```                                                                                   │
│                                                                                          │
│ Required action:                                                                         │
│ - Fix exactly the failing assertions above; do not rewrite passing code.                 │
│ - Re-run `pod verify` and read its output.                                               │
│ - Do not claim completion until `pod verify` exits 0.                                    │
│                                                                                          │
╰─────────────────────────────────────────────────────────── paste this back to the agent ─╯
tip: add --copy to put the prompt on your clipboard
```

`pod verify --fix-prompt --copy` puts that block on your clipboard; `--json` gives the same
verdict as data for an agent loop or a CI annotation.

## Four gates, one exit code

| Gate | Module | Question it answers | Fail means |
| --- | --- | --- | --- |
| `changes` | `diff_inspector.py` | Is there a concrete diff at all? | the claim is empty |
| `syntax` | `syntax_gate.py` | Does every changed `.py` file parse? | the tree cannot even import |
| `anti-dummy` | `syntax_gate.py` | Are the asserts capable of failing? | the tests are theatre |
| `tests` | `test_runner.py` | Does the suite exit `0`? | the behaviour is broken |

Detailed behaviour:

- **changes** — reads `git status --porcelain -z` and `git diff --numstat`, so staged, unstaged
  and untracked files all count. `__pycache__`, `.venv`, `.DS_Store` and friends are filtered out:
  a verification run must never mistake its own artefacts for work. `--base <rev>` audits a
  committed range instead of the working tree.
- **syntax** — `ast.parse` on every changed Python file, with the real line/column reported back.
  Then the anti-dummy scan: an `assert` whose condition is constant truthiness
  (`assert True`, `assert 1 == 1`, `assert x == x`, `assert not False`) proves nothing. If *all*
  asserts in a test module are hollow, the file is flagged as a cheating test.
- **tests** — auto-detects pytest (preferred, it also collects `unittest.TestCase`) or falls back
  to `unittest`, runs headless with a timeout, and believes the exit code. Failures are parsed into
  `file:line`, test id, error type and the short traceback.
- **fail-closed** — a repository with no tests cannot pass. An unverified claim is not a
  verified one.

Gates run in order and short-circuit: tests are pointless while the tree does not parse, so a
syntax failure stops there instead of producing 200 lines of collection errors.

## Install

```bash
# from source, isolated
uv tool install git+https://github.com/ArdaTkn/proof-of-done

# or with pip
pip install git+https://github.com/ArdaTkn/proof-of-done

# inside a project
uv add --dev git+https://github.com/ArdaTkn/proof-of-done
```

`pod` and `proof-of-done` are the same entry point. `python -m proof_of_done` works too.

## Usage

```console
$ pod check [PATH] [--base REV] [--python PATH] [--timeout SECONDS]
           [--fix-prompt] [--copy] [--json] [--quiet]
```

```console
$ pod verify                      # audit the working tree
$ pod verify --json               # machine-readable report
$ pod check --base origin/main    # audit everything since main
$ pod check --fix-prompt          # print the corrective prompt
$ pod check --copy                # ... and put it on the clipboard
$ pod check --python .venv/bin/python   # force the project interpreter
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | verified — diff, parsing code, green suite |
| `1` | rejected — at least one gate failed |
| `2` | the checker itself could not run |

## Wire it in

**Git hook** (`.git/hooks/pre-commit`) — no "done" commit with a red suite:

```bash
#!/bin/sh
exec pod check --quiet
```

**pre-commit** — add the hook from this repo:

```yaml
repos:
  - repo: https://github.com/ArdaTkn/proof-of-done
    rev: v0.1.0
    hooks:
      - id: proof-of-done
```

**GitHub Actions** — audit the whole branch, not just the last edit:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- run: pip install git+https://github.com/ArdaTkn/proof-of-done
- run: pod verify --base origin/${{ github.base_ref }}
```

**Agent loop** — the interesting one. Give the model a way to check itself and a refusal it
cannot argue with:

```python
import json, subprocess

pod = subprocess.run(["pod", "verify", "--json"], capture_output=True, text=True)
report = json.loads(pod.stdout)
if not report["ok"]:
    # report["fix_prompt"] names the failing file, line and assertion
    next_message = report["fix_prompt"]
```

Or put it in the system prompt: *"Before you claim the task is complete, run `pod verify`. If it
exits non-zero, you are not done."*

## What it does not do

Honest limits, because a verification tool that oversells itself would be ironic:

- It cannot judge whether the diff *implements the requirement* — only that a change exists and
  that the project's own suite accepts it. Existence of tests is not correctness of tests; the
  anti-dummy check catches hollow asserts, not a weak assertion about the wrong thing.
- Only Python files are parsed. Non-Python changes are counted in the diff but never linted or
  compiled (this is a known gap, not a promise).
- Test discovery is heuristic (`test_*.py`, `*_test.py`, `tests/`) — a suite configured in a way
  those patterns miss will be reported as "no tests found" and rejected.
- It runs the suite once, in place. No sandboxing, no network isolation.

## Development

```bash
git clone https://github.com/ArdaTkn/proof-of-done && cd proof-of-done
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The project verifies itself: `pod check --base origin/main` runs on every pull request.

```
src/proof_of_done/
├── diff_inspector.py      # gate 1: is there a real change?
├── syntax_gate.py         # gate 2: does it parse? are the asserts real?
├── test_runner.py         # gate 3: does the suite pass?
├── feedback_generator.py  # the surgical prompt for a rejected claim
└── cli.py                 # rich terminal interface, exit codes
tests/                     # 64 tests, no network, no fixtures to maintain
```

## License

MIT © Arda Tekin

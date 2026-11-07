"""proof-of-done: a deterministic verification gate for AI coding agents.

The package is deliberately split into four independent gates plus a renderer:

* :mod:`proof_of_done.diff_inspector` -- proof that something actually changed.
* :mod:`proof_of_done.syntax_gate`   -- proof that the changed code parses and
  the tests are not hollow.
* :mod:`proof_of_done.test_runner`   -- proof that the suite actually passes.
* :mod:`proof_of_done.feedback_generator` -- the surgical prompt an agent can
  act on when a gate rejects the claim.
* :mod:`proof_of_done.cli`           -- ``pod verify`` / ``pod check``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

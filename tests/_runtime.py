"""Locate a real HAPPI reference runtime for the conformance tests.

Why this exists
---------------
These tests used to point at a single absolute path under one developer's home
directory. On that one machine they ran; everywhere else — every other
developer, every CI runner — they skipped. A skip is reported as success, so
the suite looked green while its only end-to-end checks had never executed.
That is worse than a failing test, because nothing draws attention to it.

Resolution order, first hit wins:

  1. $HAPPI_MD               explicit override, and what CI should set
  2. $HAL_DIR/happi.md       a configured HAL checkout
  3. ~/.hal/happi.md         the conventional location
  4. ~/.hal/HAPPI.md         same, capitalised as the spec file is

`skip_no_runtime` still skips when none is found, because a developer without
a HAL checkout should not see a hard failure. The difference is that it now
finds the runtime whenever one is actually present, instead of only on one
machine.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

#: Set this to point the conformance tests at a specific runtime file.
ENV_VAR = "HAPPI_MD"


def _candidates() -> list[Path]:
    found: list[Path] = []
    explicit = os.environ.get(ENV_VAR)
    if explicit:
        found.append(Path(explicit).expanduser())
    hal_dir = os.environ.get("HAL_DIR")
    if hal_dir:
        found.append(Path(hal_dir).expanduser() / "happi.md")
    home = Path.home()
    found.append(home / ".hal" / "happi.md")
    found.append(home / ".hal" / "HAPPI.md")
    return found


def reference_runtime_path() -> Path | None:
    """First readable candidate, or None if the machine has no HAL checkout."""
    for candidate in _candidates():
        if candidate.is_file():
            return candidate
    return None


def reference_runtime_cmd() -> list[str] | None:
    """Argv that runs the reference runtime, or None if unavailable."""
    path = reference_runtime_path()
    if path is None or shutil.which("bash") is None:
        return None
    return ["bash", str(path), "run"]


RUNTIME_CMD = reference_runtime_cmd()

skip_no_runtime = pytest.mark.skipif(
    RUNTIME_CMD is None,
    reason=(
        f"no HAPPI reference runtime found — set ${ENV_VAR} or place one at "
        "~/.hal/happi.md to run the conformance tests"
    ),
)

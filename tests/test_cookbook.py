"""docs/cookbook.md must stay runnable — examples don't rot.

Two checks:
1. Every ```python block in docs/cookbook.md must COMPILE (catches syntax
   rot before any agent copies a broken snippet).
2. Blocks marked ``# offline:`` are EXECUTED with the package importable —
   they construct library results and run typed pipelines midstream, so an
   API-shape change (result fields, constructor args, step validation) fails
   here, in the suite, instead of during a live session.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

COOKBOOK = (Path(__file__).resolve().parent.parent / "docs" / "cookbook.md")


def _blocks() -> list[tuple[int, str]]:
    """(line, source) for every python block in the cookbook."""
    src = COOKBOOK.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"```python\n(.*?)```", src, re.S):
        line = src.count("\n", 0, m.start()) + 1
        out.append((line, textwrap.dedent(m.group(1))))
    return out


def test_cookbook_all_blocks_compile():
    blocks = _blocks()
    assert blocks, "docs/cookbook.md has no python blocks — is it there?"
    for line, src in blocks:
        try:
            compile(src, f"<cookbook.md:{line}>", "exec")
        except SyntaxError as e:  # pragma: no cover — failure path
            raise AssertionError(
                f"cookbook block at line {line} does not compile: "
                f"{e.msg} (line {e.lineno})") from e


def test_cookbook_offline_blocks_run():
    """Exec blocks marked ``# offline:`` — imports resolve, results construct,
    pipelines validate and fold."""
    run_blocks = [(ln, s) for ln, s in _blocks()
                  if re.search(r"^\s*# offline:", s, re.M)]
    assert run_blocks, "no offline blocks found — add the '# offline:' marker"
    for line, src in run_blocks:
        ns: dict = {}
        try:
            exec(compile(src, f"<cookbook.md:{line}>", "exec"), ns)
        except AssertionError as e:  # the block's own self-checks
            raise AssertionError(
                f"cookbook offline block at line {line} FAILED its "
                f"assertions: {e}") from e
        except Exception as e:  # API shape changed / import broke
            raise AssertionError(
                f"cookbook offline block at line {line} raised "
                f"{type(e).__name__}: {e}") from e
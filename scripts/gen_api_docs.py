#!/usr/bin/env python3
"""Auto-generate API docs from the live package (docstrings + signatures).

  scripts/python scripts/gen_api_docs.py

Writes:
  docs/api.md                — full Markdown reference (renders on GitHub)
  docs/index.html            — refreshes the compact "API at a glance" table

Dependency-free (stdlib `inspect` only), so it always matches the repo.
"""

from __future__ import annotations

import html as _html
import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import mtbls_agent  # noqa: E402
from mtbls_agent import __all__  # noqa: E402


def first_para(doc: str | None, max_chars: int = 220) -> str:
    """First paragraph of a docstring, normalized to plain text."""
    if not doc:
        return ""
    text = re.sub(r"<[^>]+>", " ", doc)          # strip inline reST/HTML
    text = re.sub(r"``([^`]+)``", r"`\1`", text)  # double-backtick -> code
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    end = text.find(". ")
    if max_chars and len(text) > max_chars:
        cut = text[: max_chars].rsplit(" ", 1)[0]
        return cut + "…"
    return text


def signature(obj) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "…"


def classify(obj):
    if inspect.isclass(obj):
        return "class"
    if inspect.isfunction(obj):
        return "function"
    return "other"


def build():
    # ── functions vs constructors vs returned dataclasses ──
    funcs, classes = [], []
    for name in __all__:
        obj = getattr(mtbls_agent, name, None)
        if obj is None:
            continue
        (funcs if classify(obj) == "function" else classes).append(name)

    funcs.sort()
    classes.sort()
    doc = ["# mtbls_agent — API reference (auto-generated)", "",
           "> Regenerate anytime: `scripts/python scripts/gen_api_docs.py`", ""]

    doc += ["## Functions", ""]
    for name in funcs:
        obj = getattr(mtbls_agent, name)
        doc += [f"### `{name}{signature(obj)}`", "",
                first_para(inspect.getdoc(obj), max_chars=400), ""]

    doc += ["## Classes / constructors", ""]
    for name in classes:
        obj = getattr(mtbls_agent, name)
        doc += [f"### `{name}{signature(obj)}`", "",
                first_para(inspect.getdoc(obj), max_chars=400), ""]
        if inspect.isclass(obj) and hasattr(obj, "__dataclass_fields__"):
            for f, fld in obj.__dataclass_fields__.items():
                docta = first_para(getattr(fld, "doc", ""), max_chars=120)
                docta = f" — {docta}" if docta else ""
                doc.append(f"- `{f}`: `{_plain(fld.type)}`{docta}")
            doc.append("")

    (REPO / "docs" / "api.md").write_text("\n".join(doc).rstrip() + "\n")

    # ── compact table for index.html ──
    rows = []
    for name in funcs + classes:
        obj = getattr(mtbls_agent, name)
        purpose = first_para(inspect.getdoc(obj), max_chars=96)
        rows.append((name, purpose))
    table_rows = "\n".join(
        f"      <tr><td><code>{_html.escape(n)}</code></td>"
        f"<td>{_html.escape(p)}</td></tr>"
        for n, p in rows if p
    )
    html_path = REPO / "docs" / "index.html"
    text = html_path.read_text()
    marker_start = "<tbody id=\"api-rows\">"
    marker_end = "</tbody>"
    block = f"<tbody id=\"api-rows\">\n{table_rows}\n    </tbody>"
    if marker_start in text and marker_end in text:
        s = text.index(marker_start)
        e = text.index(marker_end, s) + len(marker_end)
        html_path.write_text(text[:s] + block + text[e:])
    print(f"wrote docs/api.md ({len(doc)} lines) + refreshed docs/index.html table")
    print(f"({len(funcs)} functions, {len(classes)} classes)")


def _plain(t) -> str:
    return re.sub(r"<class '([^']+)'>", r"\1", str(t)).replace("typing.", "")


if __name__ == "__main__":
    build()
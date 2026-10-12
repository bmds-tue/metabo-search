#!/usr/bin/env bash
# Install the metabo-search skill:
#   1) set up a repo-local venv (.venv-local) and pip-install the package
#   2) symlink this repo into each agent's skills directory (pi, claude, opencode, .agents)
#   3) optionally validate with skills-ref when available
#
# Safe to re-run: re-links skill dirs, re-installs the package in-place.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_NAME="metabo-search"

echo "==> Repo: $REPO"

# ── 1) Python env ─────────────────────────────────────────────────────────────
VENV_PY="$REPO/.venv-local/bin/python"
# ``-x`` is not enough: a venv whose interpreter cannot load (e.g. a missing
# libpython) still passes. Probe it for real and recreate if it is broken.
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -c "import sys" >/dev/null 2>&1; then
    echo "==> (re)creating .venv-local"
    rm -rf "$REPO/.venv-local"
    if command -v uv >/dev/null 2>&1; then
        uv venv "$REPO/.venv-local"
    else
        python3 -m venv "$REPO/.venv-local"
    fi
fi

echo "==> install package + dev extras"
if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_PY" -e "$REPO[dev]"
else
    "$VENV_PY" -m pip install -e "$REPO[dev]"
fi

# ── 2) Link the skill into agent skills dirs ──────────────────────────────────
# Override any destination via env, e.g. PI_SKILLS=~/custom/skills.
DEST_DIRS=()
[ -n "${PI_SKILLS:-}" ] && DEST_DIRS+=("$PI_SKILLS")
[ -n "${CLAUDE_SKILLS:-}" ] && DEST_DIRS+=("$CLAUDE_SKILLS")
[ -n "${OPENCODE_SKILLS:-}" ] && DEST_DIRS+=("$OPENCODE_SKILLS")
DEST_DIRS+=(
    "$HOME/.pi/agent/skills"
    "$HOME/.agents/skills"
    "$HOME/.claude/skills"
    "$HOME/.config/opencode/skills"
    "$HOME/.opencode/skills"
)

for d in "${DEST_DIRS[@]}"; do
    [ -z "$d" ] && continue
    mkdir -p "$d"
    target="$d/$SKILL_NAME"
    if [ -L "$target" ] || [ -e "$target" ]; then
        # Replace stale symlink; leave real dirs alone.
        if [ -L "$target" ]; then rm -f "$target"; else echo "    keep existing dir: $target"; continue; fi
    fi
    ln -s "$REPO" "$target"
    echo "    linked $target  ->  $REPO"
done

# ── 3) Validate (optional) ────────────────────────────────────────────────────
if command -v skills-ref >/dev/null 2>&1; then
    echo "==> skills-ref validate"
    skills-ref validate "$REPO" || true
fi

echo
echo "Done."
echo "  venv python:   $REPO/.venv-local/bin/python"
echo "  run scripts:   $REPO/scripts/python -c 'import metabo_search'"
echo "  uninstall:     $REPO/scripts/uninstall.sh"
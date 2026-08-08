#!/usr/bin/env bash
# Install the metabolights-search skill:
#   1) set up a repo-local venv (.venv-local) and pip-install the package
#   2) symlink this repo into each agent's skills directory (pi, claude, opencode, .agents)
#   3) optionally validate with skills-ref when available
#
# Safe to re-run: re-links skill dirs, re-installs the package in-place.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_NAME="metabolights-search"

echo "==> Repo: $REPO"

# ── 1) Python env ─────────────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    if [ ! -x "$REPO/.venv-local/bin/python" ]; then
        echo "==> uv venv .venv-local"
        uv venv "$REPO/.venv-local"
    fi
    echo "==> uv pip install -e ."
    uv pip install --python "$REPO/.venv-local/bin/python" -e "$REPO"
else
    echo "==> uv not found; using python3 -m venv"
    python3 -m venv "$REPO/.venv-local"
    "$REPO/.venv-local/bin/pip" install -e "$REPO"
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
echo "  run scripts:   $REPO/scripts/python -c 'import mtbls_agent'"
echo "  uninstall:     $REPO/scripts/uninstall.sh"
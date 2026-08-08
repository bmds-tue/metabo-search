#!/usr/bin/env bash
# Remove the skill symlinks installed by install.sh. Venv is left in place.
set -euo pipefail
SKILL_NAME="metabolights-search"
for d in "$HOME/.pi/agent/skills" "$HOME/.agents/skills" "$HOME/.claude/skills" \
         "$HOME/.config/opencode/skills" "$HOME/.opencode/skills"; do
    t="$d/$SKILL_NAME"
    if [ -L "$t" ]; then rm -f "$t"; echo "removed: $t"; fi
done
echo "Done (venv kept: .venv-local/)."

#!/usr/bin/env bash
# Remove the skill symlinks installed by install.sh. Venv is left in place.
set -euo pipefail
SKILL_NAME="metabo-search"
# also remove links installed under the old name, if any
for name in "$SKILL_NAME" "metabolights-search"; do
    for d in "$HOME/.pi/agent/skills" "$HOME/.agents/skills" "$HOME/.claude/skills" \
             "$HOME/.config/opencode/skills" "$HOME/.opencode/skills"; do
        t="$d/$name"
        if [ -L "$t" ]; then rm -f "$t"; echo "removed: $t"; fi
    done
done
echo "Done (venv kept: .venv-local/)."

#!/bin/bash
# Sync Claude Code and Codex sessions to Eli's server
# Claude sessions are synced like before; Codex sessions keep their date tree.
# Usage: ./sync-to-eli.sh [username]

USER="${1:-anton}"
CLAUDE_DIR="$HOME/.claude/projects"
CODEX_DIR="$HOME/.codex/sessions"
TARGET="eli@82.165.138.182:/home/eli/geist/archive/$USER/"
CODEX_TARGET="eli@82.165.138.182:/home/eli/geist/archive/codex/"

if [ ! -d "$CLAUDE_DIR" ]; then
    echo "Error: $CLAUDE_DIR not found"
    exit 1
fi

# Count total JSONL files across all projects
COUNT=$(find "$CLAUDE_DIR" -name '*.jsonl' 2>/dev/null | wc -l)
DIRS=$(ls -d "$CLAUDE_DIR"/*/ 2>/dev/null | wc -l)

echo "Syncing $COUNT sessions from $DIRS projects for $USER..."

# Sync each project directory
for dir in "$CLAUDE_DIR"/*/; do
    LOCAL_COUNT=$(ls "$dir"*.jsonl 2>/dev/null | wc -l)
    [ "$LOCAL_COUNT" -eq 0 ] && continue

    rsync -az \
        --include='*.jsonl' \
        --exclude='*' \
        "$dir" "$TARGET"
done

echo "Done. $COUNT sessions synced."

if [ -d "$CODEX_DIR" ]; then
    CODEX_COUNT=$(find "$CODEX_DIR" -name '*.jsonl' 2>/dev/null | wc -l)

    if [ "$CODEX_COUNT" -gt 0 ]; then
        echo "Syncing $CODEX_COUNT Codex sessions..."
        rsync -az \
            --include='*/' \
            --include='*.jsonl' \
            --exclude='*' \
            "$CODEX_DIR/" "$CODEX_TARGET"
        echo "Done. $CODEX_COUNT Codex sessions synced."
    else
        echo "No Codex sessions found in $CODEX_DIR."
    fi
else
    echo "Skipping Codex: $CODEX_DIR not found."
fi

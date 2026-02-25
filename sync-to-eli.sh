commit a04b3edd7fa023181fb81370833fa87f233669e7
Author: Anton Tranelis <mail@antontranelis.de>
Date:   Wed Feb 25 14:28:14 2026 +0100

    Initial commit: Session Archive mit Knowledge Graph
    
    Session-Archive Server mit Neo4j Knowledge Graph Integration:
    - serve.py: HTTP-Server mit Session-API, Semantik-Destillation und Graph-Queries
    - build.py: Session-Analyse und Zusammenfassung
    - index.html: Interaktive D3.js Graph-Visualisierung
    - Dockerfile: Container-Setup für Deployment
    - sync-to-eli.sh: JSONL-Sync zu Eli's Server
    
    Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>

diff --git a/sync-to-eli.sh b/sync-to-eli.sh
new file mode 100755
index 0000000..f232234
--- /dev/null
+++ b/sync-to-eli.sh
@@ -0,0 +1,32 @@
+#!/bin/bash
+# Sync Claude Code sessions to Eli's server
+# Syncs ALL project directories' JSONL files
+# Usage: ./sync-to-eli.sh [username]
+
+USER="${1:-anton}"
+CLAUDE_DIR="$HOME/.claude/projects"
+TARGET="eli@82.165.138.182:/home/eli/geist/archive/$USER/"
+
+if [ ! -d "$CLAUDE_DIR" ]; then
+    echo "Error: $CLAUDE_DIR not found"
+    exit 1
+fi
+
+# Count total JSONL files across all projects
+COUNT=$(find "$CLAUDE_DIR" -name '*.jsonl' 2>/dev/null | wc -l)
+DIRS=$(ls -d "$CLAUDE_DIR"/*/ 2>/dev/null | wc -l)
+
+echo "Syncing $COUNT sessions from $DIRS projects for $USER..."
+
+# Sync each project directory
+for dir in "$CLAUDE_DIR"/*/; do
+    LOCAL_COUNT=$(ls "$dir"*.jsonl 2>/dev/null | wc -l)
+    [ "$LOCAL_COUNT" -eq 0 ] && continue
+
+    rsync -az \
+        --include='*.jsonl' \
+        --exclude='*' \
+        "$dir" "$TARGET"
+done
+
+echo "Done. $COUNT sessions synced."

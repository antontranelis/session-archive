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

diff --git a/build.py b/build.py
new file mode 100644
index 0000000..ab09b0a
--- /dev/null
+++ b/build.py
@@ -0,0 +1,395 @@
+#!/usr/bin/env python3
+"""
+Session Archive Builder
+=======================
+
+Reads all Claude Code JSONL session files and generates:
+- Individual markdown files per session
+- An HTML index page for browsing all sessions
+
+Usage: python3 build.py
+"""
+
+import json
+import os
+import glob
+import re
+import html
+from datetime import datetime, timezone
+from pathlib import Path
+
+BASE_DIR = "/home/fritz/.claude/projects/-home-fritz-workspace-workspace"
+OUT_DIR = Path(__file__).parent / "sessions"
+INDEX_FILE = Path(__file__).parent / "index.html"
+
+
+def strip_tags(text: str) -> str:
+    """Remove XML-like tags (ide_selection, system-reminder, etc.)."""
+    # Remove entire system-reminder blocks
+    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
+    # Remove ide_selection blocks
+    text = re.sub(r'<ide_selection>.*?</ide_selection>', '', text, flags=re.DOTALL)
+    # Remove ide_opened_file blocks
+    text = re.sub(r'<ide_opened_file>.*?</ide_opened_file>', '', text, flags=re.DOTALL)
+    # Remove command-message / command-name blocks
+    text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
+    text = re.sub(r'<command-name>.*?</command-name>', '', text, flags=re.DOTALL)
+    return text.strip()
+
+
+def extract_text(content) -> str:
+    """Extract readable text from message content (string or list)."""
+    if isinstance(content, str):
+        return strip_tags(content)
+    if isinstance(content, list):
+        parts = []
+        for item in content:
+            if isinstance(item, dict):
+                if item.get("type") == "text":
+                    parts.append(strip_tags(item.get("text", "")))
+                elif item.get("type") == "tool_use":
+                    name = item.get("name", "")
+                    # Skip noisy tool calls, keep interesting ones
+                    if name in ("mcp__eli__eli_init", "mcp__eli__eli_memory_search",
+                                "mcp__eli__eli_memory_save", "mcp__eli__eli_telegram_send"):
+                        inp = item.get("input", {})
+                        if name == "mcp__eli__eli_memory_search":
+                            parts.append(f"[Eli sucht: {inp.get('query', '')}]")
+                        elif name == "mcp__eli__eli_memory_save":
+                            parts.append(f"[Eli speichert Erinnerung]")
+                        elif name == "mcp__eli__eli_telegram_send":
+                            parts.append(f"[Eli sendet Telegram an {inp.get('recipient', '')}]")
+                        elif name == "mcp__eli__eli_init":
+                            parts.append("[Eli initialisiert sich]")
+        return "\n".join(p for p in parts if p)
+    return ""
+
+
+def parse_session(filepath: str) -> dict:
+    """Parse a JSONL session file into structured data."""
+    messages = []
+    session_id = os.path.basename(filepath).replace(".jsonl", "")
+    first_ts = None
+    last_ts = None
+
+    with open(filepath) as f:
+        for line in f:
+            try:
+                obj = json.loads(line)
+            except json.JSONDecodeError:
+                continue
+
+            msg_type = obj.get("type", "")
+            if msg_type not in ("user", "assistant", "summary"):
+                continue
+
+            msg = obj.get("message", {})
+            content = msg.get("content", "")
+            ts_raw = msg.get("createdAt") or obj.get("timestamp")
+
+            # Parse timestamp
+            ts = None
+            if isinstance(ts_raw, str):
+                try:
+                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
+                except ValueError:
+                    pass
+            elif isinstance(ts_raw, (int, float)):
+                try:
+                    ts = datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
+                except (ValueError, OSError):
+                    pass
+
+            if ts and not first_ts:
+                first_ts = ts
+            if ts:
+                last_ts = ts
+
+            text = extract_text(content)
+            if not text:
+                continue
+
+            # For summaries, mark them
+            if msg_type == "summary":
+                role = "system"
+                text = f"--- Session kompaktiert ---\n{text[:500]}"
+            else:
+                role = msg.get("role", msg_type)
+
+            messages.append({
+                "role": role,
+                "text": text,
+                "timestamp": ts,
+            })
+
+    if not messages:
+        return None
+
+    # First meaningful user message as title
+    title = ""
+    for m in messages:
+        if m["role"] in ("user", "human"):
+            clean = m["text"].strip()
+            if clean and len(clean) > 3:
+                title = clean[:120]
+                break
+
+    if not title:
+        title = f"Session {session_id[:8]}"
+
+    return {
+        "id": session_id,
+        "title": title,
+        "messages": messages,
+        "first_ts": first_ts,
+        "last_ts": last_ts,
+        "msg_count": len(messages),
+    }
+
+
+def write_session_md(session: dict):
+    """Write a single session as markdown file."""
+    OUT_DIR.mkdir(parents=True, exist_ok=True)
+    date_str = session["first_ts"].strftime("%Y-%m-%d") if session["first_ts"] else "unknown"
+    filename = f"{date_str}_{session['id'][:8]}.md"
+    filepath = OUT_DIR / filename
+
+    lines = []
+    lines.append(f"# {session['title']}")
+    lines.append(f"")
+    lines.append(f"**Datum:** {date_str}")
+    lines.append(f"**Nachrichten:** {session['msg_count']}")
+    lines.append(f"**Session:** `{session['id']}`")
+    lines.append(f"")
+    lines.append("---")
+    lines.append("")
+
+    for msg in session["messages"]:
+        role = msg["role"]
+        ts = msg["timestamp"].strftime("%H:%M") if msg["timestamp"] else ""
+
+        if role in ("user", "human"):
+            lines.append(f"### Anton {ts}")
+        elif role == "assistant":
+            lines.append(f"### Eli {ts}")
+        elif role == "system":
+            lines.append(f"### System {ts}")
+        else:
+            lines.append(f"### {role} {ts}")
+
+        lines.append("")
+        lines.append(msg["text"])
+        lines.append("")
+
+    filepath.write_text("\n".join(lines), encoding="utf-8")
+    return filename
+
+
+def write_index(sessions: list):
+    """Write HTML index page."""
+    sessions_sorted = sorted(sessions, key=lambda s: s["first_ts"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
+
+    rows = []
+    total_msgs = 0
+    for s in sessions_sorted:
+        date_str = s["first_ts"].strftime("%Y-%m-%d") if s["first_ts"] else "?"
+        time_str = s["first_ts"].strftime("%H:%M") if s["first_ts"] else ""
+        filename = f"{date_str}_{s['id'][:8]}.md"
+        title_esc = html.escape(s["title"][:100])
+        total_msgs += s["msg_count"]
+        rows.append(f"""      <tr>
+        <td class="date">{date_str} <span class="time">{time_str}</span></td>
+        <td><a href="sessions/{filename}">{title_esc}</a></td>
+        <td class="num">{s['msg_count']}</td>
+      </tr>""")
+
+    index_html = f"""<!DOCTYPE html>
+<html lang="de">
+<head>
+<meta charset="UTF-8">
+<meta name="viewport" content="width=device-width, initial-scale=1.0">
+<title>Eli & Anton — Session-Archiv</title>
+<style>
+  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
+  body {{
+    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
+    background: #0f172a;
+    color: #e2e8f0;
+    padding: 2rem;
+    max-width: 900px;
+    margin: 0 auto;
+  }}
+  h1 {{
+    font-size: 1.8rem;
+    margin-bottom: 0.3rem;
+    background: linear-gradient(135deg, #a78bfa, #60a5fa);
+    -webkit-background-clip: text;
+    -webkit-text-fill-color: transparent;
+  }}
+  .subtitle {{
+    color: #94a3b8;
+    margin-bottom: 2rem;
+    font-size: 0.95rem;
+  }}
+  .stats {{
+    display: flex;
+    gap: 2rem;
+    margin-bottom: 2rem;
+    flex-wrap: wrap;
+  }}
+  .stat {{
+    background: #1e293b;
+    padding: 1rem 1.5rem;
+    border-radius: 0.5rem;
+    border: 1px solid #334155;
+  }}
+  .stat-num {{
+    font-size: 1.5rem;
+    font-weight: 700;
+    color: #a78bfa;
+  }}
+  .stat-label {{
+    font-size: 0.8rem;
+    color: #94a3b8;
+    margin-top: 0.2rem;
+  }}
+  table {{
+    width: 100%;
+    border-collapse: collapse;
+  }}
+  th {{
+    text-align: left;
+    padding: 0.6rem 0.8rem;
+    border-bottom: 2px solid #334155;
+    color: #94a3b8;
+    font-size: 0.8rem;
+    text-transform: uppercase;
+    letter-spacing: 0.05em;
+  }}
+  td {{
+    padding: 0.6rem 0.8rem;
+    border-bottom: 1px solid #1e293b;
+    font-size: 0.9rem;
+  }}
+  tr:hover {{ background: #1e293b; }}
+  a {{
+    color: #60a5fa;
+    text-decoration: none;
+  }}
+  a:hover {{
+    color: #93bbfc;
+    text-decoration: underline;
+  }}
+  .date {{
+    white-space: nowrap;
+    color: #94a3b8;
+    font-family: 'SF Mono', 'Consolas', monospace;
+    font-size: 0.85rem;
+  }}
+  .time {{
+    color: #475569;
+    font-size: 0.75rem;
+  }}
+  .num {{
+    text-align: right;
+    color: #94a3b8;
+    font-family: 'SF Mono', 'Consolas', monospace;
+  }}
+  .search-box {{
+    width: 100%;
+    padding: 0.7rem 1rem;
+    background: #1e293b;
+    border: 1px solid #334155;
+    border-radius: 0.5rem;
+    color: #e2e8f0;
+    font-size: 0.95rem;
+    margin-bottom: 1.5rem;
+    outline: none;
+  }}
+  .search-box:focus {{
+    border-color: #60a5fa;
+  }}
+  .search-box::placeholder {{
+    color: #475569;
+  }}
+</style>
+</head>
+<body>
+
+<h1>Session-Archiv</h1>
+<p class="subtitle">Eli & Anton — alle Gespräche seit Januar 2026</p>
+
+<div class="stats">
+  <div class="stat">
+    <div class="stat-num">{len(sessions_sorted)}</div>
+    <div class="stat-label">Sessions</div>
+  </div>
+  <div class="stat">
+    <div class="stat-num">{total_msgs:,}</div>
+    <div class="stat-label">Nachrichten</div>
+  </div>
+  <div class="stat">
+    <div class="stat-num">{sessions_sorted[0]["first_ts"].strftime("%d.%m.%Y") if sessions_sorted else "?"}</div>
+    <div class="stat-label">Neueste Session</div>
+  </div>
+</div>
+
+<input type="text" class="search-box" placeholder="Suche in Titeln..." oninput="filterRows(this.value)">
+
+<table>
+  <thead>
+    <tr>
+      <th>Datum</th>
+      <th>Thema</th>
+      <th style="text-align:right">Msgs</th>
+    </tr>
+  </thead>
+  <tbody id="sessions">
+{"".join(rows)}
+  </tbody>
+</table>
+
+<script>
+function filterRows(query) {{
+  const q = query.toLowerCase();
+  document.querySelectorAll('#sessions tr').forEach(tr => {{
+    const text = tr.textContent.toLowerCase();
+    tr.style.display = text.includes(q) ? '' : 'none';
+  }});
+}}
+</script>
+
+</body>
+</html>"""
+
+    INDEX_FILE.write_text(index_html, encoding="utf-8")
+
+
+def main():
+    print("Lese Sessions...")
+    files = sorted(glob.glob(os.path.join(BASE_DIR, "*.jsonl")))
+    print(f"  {len(files)} JSONL-Dateien gefunden")
+
+    sessions = []
+    for f in files:
+        session = parse_session(f)
+        if session and session["msg_count"] >= 2:
+            sessions.append(session)
+
+    print(f"  {len(sessions)} Sessions mit Inhalt")
+    print(f"  {sum(s['msg_count'] for s in sessions):,} Nachrichten gesamt")
+
+    print("Schreibe Markdown-Dateien...")
+    for s in sessions:
+        write_session_md(s)
+
+    print("Schreibe Index...")
+    write_index(sessions)
+
+    print(f"\nFertig!")
+    print(f"  Index: {INDEX_FILE}")
+    print(f"  Sessions: {OUT_DIR}/")
+
+
+if __name__ == "__main__":
+    main()

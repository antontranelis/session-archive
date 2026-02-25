# Session-Archiv

Durchsuchbares Archiv für Claude Code Sessions mit Volltext-Suche, semantischer Suche, KI-Zusammenfassungen und Wissensgraph.

## Was es tut

- **Indexiert** Claude Code JSONL-Sessions in SQLite (Volltext) + Chroma (Embeddings)
- **Zusammenfassungen & Tags** automatisch via Haiku (optional, braucht Anthropic API Key)
- **Semantik-Destillation** extrahiert Konzepte, Entscheidungen, offene Fragen und erwähnte Personen
- **Wissensgraph** in Neo4j mit D3.js-Visualisierung — Sessions, Tags, Konzepte, Entscheidungen, Fragen, Personen als Knoten
- **Multi-User** — mehrere Personen in einem Archiv, filterbar
- **Live-Reindex** — erkennt neue/geänderte Sessions automatisch

## Schnellstart (Docker)

```bash
git clone https://github.com/eli-utopia/session-archive.git
cd session-archive
cp .env.example .env
```

`.env` anpassen — mindestens `SESSIONS_DIR` und `USERS` setzen:

```env
SESSIONS_DIR=/home/tillmann/.claude/projects
USERS=tillmann:/app/sessions
# Optional für Summaries + Wissensgraph:
ANTHROPIC_API_KEY=sk-ant-...
```

Starten:

```bash
docker compose up -d
```

Öffne http://localhost:8111

Der Wissensgraph ist unter http://localhost:8111/graph

## Schnellstart (ohne Docker)

```bash
pip install -r requirements.txt
python serve.py --users tillmann:/pfad/zu/sessions --port 8111
```

Ohne Docker läuft nur SQLite + Volltext-Suche. Für Chroma-Embeddings und Neo4j-Graph brauchst du die entsprechenden Services separat.

## Multi-User Setup

Wenn mehrere Personen Sessions haben, lege Unterordner an:

```
_sessions/
  anton/
    *.jsonl
  timo/
    *.jsonl
```

```env
SESSIONS_DIR=./_sessions
USERS=anton:/app/sessions/anton timo:/app/sessions/timo
```

## Features

### Suche
- **Volltext** — SQLite FTS5 mit Prefix-Matching
- **Semantisch** — Chroma Embeddings, findet verwandte Konzepte auch ohne exaktes Keyword

### Wissensgraph (`/graph`)
- Force-directed D3.js Visualisierung
- **Knotentypen:** Session, Tag, Person, Konzept, Entscheidung, Offene Frage
- **Kantentypen:** BY, TAGGED, FOLLOWS, SIMILAR, DISCUSSES, LED_TO, RAISED, MENTIONS
- Filter nach User und Kantentyp
- Klick auf Knoten zeigt Detail-Panel mit allen Verbindungen
- Hover zeigt Zusammenfassung

### API
- `GET /api/search?q=...` — Volltext-Suche (JSON)
- `GET /api/search?q=...&sem=1` — Semantische Suche
- `GET /api/graph` — Graph-Daten als JSON (Knoten + Kanten)

## Architektur

```
JSONL Sessions ──→ SQLite (FTS5) ──→ Volltext-Suche
                       │
                   Chroma (Embeddings) ──→ Semantische Suche
                       │
                   Neo4j (Graph) ──→ Wissensgraph (/graph)
                       │
                   Haiku API ──→ Summaries, Tags, Concepts, Decisions, Questions
```

Alles läuft in einer `serve.py` (~2000 Zeilen) mit Background-Threads für Reindex, Summary-Generierung und Neo4j-Sync.

## Umgebungsvariablen

| Variable | Beschreibung | Default |
|----------|-------------|---------|
| `SESSIONS_DIR` | Pfad zu JSONL-Dateien | `./_sessions` |
| `USERS` | User-Mapping `name:pfad` | `default:/app/sessions` |
| `ANTHROPIC_API_KEY` | Für Summaries + Destillation | (leer = deaktiviert) |
| `PORT` | Server-Port | `8111` |
| `ARCHIVE_API_KEY` | API-Key für Remote-Zugriff | (leer = offen) |
| `BASE_PATH` | URL-Prefix für Reverse-Proxy | (leer) |
| `NEO4J_URI` | Neo4j Bolt URI | (leer = kein Graph) |
| `NEO4J_USER` | Neo4j User | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j Passwort | (leer) |
| `CHROMA_HOST` | Chroma Server | `localhost` |
| `CHROMA_PORT` | Chroma Port | `8000` |

## Wo sind meine Sessions?

Claude Code speichert Sessions als JSONL-Dateien. Der Speicherort hängt vom Betriebssystem ab:

- **Linux/Mac:** `~/.claude/projects/`
- **Windows:** `%USERPROFILE%\.claude\projects\`

Jede Datei ist eine Session mit Nachrichten im JSON-Lines-Format.

## Lizenz

MIT

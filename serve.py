#!/usr/bin/env python3
"""
Session Archive — Live Server with SQLite + Chroma
====================================================

Indexes Claude Code sessions into SQLite (FTS5) + Chroma (semantic search).
Incremental: only re-parses changed JSONL files.

Start: python3 serve.py [--port 8111]
Then:  http://localhost:8111
       http://localhost:8111/?q=keyword        (fulltext)
       http://localhost:8111/?q=concept&sem=1   (semantic)
"""

import json
import os
import glob
import re
import html as html_mod
import sqlite3
import argparse
import hashlib
import threading
import time as time_mod
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Berlin")
MIN_MSG_COUNT = 3  # Sessions with fewer messages are skipped (warm-up/test sessions)
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_DIR", str(Path(__file__).parent))) / "archive.db"

def utc_to_cet(iso_str: str | None) -> str | None:
    """Convert an ISO timestamp string (UTC) to Europe/Berlin time."""
    if not iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CET).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str
PORT = 8111
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Multi-user: map user_id → directory with JSONL files
USERS = {}
DEFAULT_USER = "anton"
_default_dir = "/home/fritz/.claude/projects/-home-fritz-workspace-workspace"

# API key for remote access (set via env or --api-key)
API_KEY = os.environ.get("ARCHIVE_API_KEY", "")

# Base path prefix for reverse proxy (e.g. "/archive" when behind /archive/*)
BASE_PATH = os.environ.get("BASE_PATH", "").rstrip("/")

# --- Projekte (eigener Knotentyp im Graph) ---
PROJECTS = {
    "web-of-trust", "real-life-stack", "real-life-network", "money-printer",
    "eli", "utopia-map", "yoga-vidya", "wir-sind-wertvoll", "geburtstagsfeier",
    "session-archiv",
}

# --- Kuratiertes Tag-Set (ohne Projekte — die sind separat) ---
# Erweitert sich nur wenn wirklich eine neue Kategorie nötig ist.
ALLOWED_TAGS = {
    # Technik
    "architektur", "frontend", "deployment", "testing", "debugging",
    "datenbank", "kryptographie", "api", "performance", "sicherheit", "design",
    # Konzepte (ex-Concepts, jetzt als Tags)
    "dezentralisierung", "identität", "vertrauen", "gemeinschaft",
    "souveränität", "offline-first", "erinnerung", "messaging",
    # Mensch & Vision
    "persönliches", "vision", "reflexion", "beziehungen", "familie",
    "autonomie", "heilung", "zusammenarbeit",
    # Praxis
    "dokumentation", "finanzierung", "strategie", "recherche", "infrastruktur",
    # Erweitert
    "visualisierung", "rechtliches", "ai",
}

# Mapping: alter Tag → neuer Tag (oder None = löschen)
TAG_MIGRATION = {
    # design
    "responsive-design": "design", "responsives-design": "design",
    "ui-design": "design", "ui-ux": "design", "ui-ux-design": "design",
    "ui": "design", "ui-components": "design", "ui-layout": "design",
    "grid-layout": "design", "template-system": "design",
    "design-produktion": "design", "design-und-produktion": "design",
    "farb-management": "design", "image-processing": "design",
    "branding": "design", "web-presence": "design", "web-projekt": "design",
    # gemeinschaft
    "community": "gemeinschaft", "gesellschaft": "gemeinschaft",
    # visualisierung
    "visualization": "visualisierung", "netzwerk-darstellung": "visualisierung",
    "graphen-analyse": "visualisierung", "dashboard": "visualisierung",
    # reflexion
    "reflection": "reflexion", "selbstreflexion": "reflexion",
    "ego-reflexion": "reflexion", "ego-selbsterkenntnis": "reflexion",
    # finanzierung
    "funding": "finanzierung", "funding-grants": "finanzierung",
    "geld-und-wirtschaft": "finanzierung", "cost-optimization": "finanzierung",
    # datenbank
    "datei-verwaltung": "datenbank", "dateimanagement": "datenbank",
    "datenverwaltung": "datenbank", "speicher-verwaltung": "datenbank",
    "speicherung": "datenbank", "storage": "datenbank", "web-storage": "datenbank",
    "persistenz": "datenbank", "persistierung": "datenbank",
    "daten-migration": "datenbank", "datenmigration": "datenbank",
    "datenbereinigung": "datenbank", "vektordatenbank": "datenbank",
    "vektordatenbanken": "datenbank", "clustering": "datenbank",
    # sicherheit
    "zugriff-kontrolle": "sicherheit", "zugriffskontrolle": "sicherheit",
    "access-control": "sicherheit", "datenschutz": "sicherheit",
    "authentifizierung": "sicherheit", "verifikation": "sicherheit",
    # infrastructure
    "infrastructure": "infrastruktur", "server-konfiguration": "infrastruktur",
    "hardware": "infrastruktur", "whisper": "infrastruktur",
    # money-printer
    "money-printing": "money-printer", "geld-druck": "money-printer", "druck": "money-printer",
    # autonomie
    "autonomie-und-eigenstaendigkeit": "autonomie",
    # erinnerung
    "erinnerungen": "erinnerung", "memory-management": "erinnerung",
    "memory-system": "erinnerung", "archiv": "erinnerung",
    "archivierung": "erinnerung", "archiv-system": "erinnerung",
    # debugging
    "bug-fixes": "debugging", "bug-fixing": "debugging",
    "troubleshooting": "debugging", "fehlerbehandlung": "debugging",
    # persönliches
    "körperpflege": "persönliches", "wellness": "persönliches",
    "schmerzen": "persönliches", "gesundheit": "persönliches",
    "schweiz": "persönliches", "alltäglich": "persönliches",
    "arbeitsbereich": "persönliches", "desktop-verwaltung": "persönliches",
    # rechtliches
    "rechtlich": "rechtliches", "rechtsformen": "rechtliches",
    "lizenzierung": "rechtliches", "gemeinnützigkeit": "rechtliches",
    # identität
    "profil-management": "identität", "profil-verwaltung": "identität",
    # dezentralisierung
    "netzwerk": "dezentralisierung", "networking": "dezentralisierung",
    "netzwerk-design": "dezentralisierung", "peer-to-peer": "dezentralisierung",
    "blockchain": "dezentralisierung", "dezentral": "dezentralisierung",
    # zusammenarbeit
    "kollaboration": "zusammenarbeit", "kooperation": "zusammenarbeit",
    "kommunikation": "zusammenarbeit",
    # architektur
    "modul-entwicklung": "architektur", "modul-system": "architektur",
    "frontend-architektur": "architektur", "datenmodellierung": "architektur",
    "datenstruktur": "architektur",
    # frontend
    "demo-app": "frontend", "prototyping": "frontend", "landingpage": "frontend",
    # eli
    "remote-mcp": "eli", "mcp": "eli",
    # offline-first
    "synchronisierung": "offline-first", "lokale-erste": "offline-first",
    # ai
    "prompt-engineering": "ai", "ai-ethik": "ai", "ai-frameworks": "ai",
    "ai-integration": "ai", "ai-tools": "ai", "ki-beratung": "ai",
    "maschinelles-lernen": "ai", "gemini-api": "ai",
    # strategie
    "marktforschung": "strategie", "geschäftsentwicklung": "strategie",
    "karriere-entwicklung": "strategie", "freelancing": "strategie",
    "governance": "strategie", "it-governance": "strategie",
    # utopia-map
    "geo-daten": "utopia-map", "geographie": "utopia-map",
    # vision
    "nachhaltigkeit": "vision", "philosophie": "vision",
    # testing
    "qualitätssicherung": "testing", "linting": "testing",
    "code-qualität": "testing", "code-review": "testing", "refactoring": "testing",
    # api
    "api-integration": "api",
    # entfernen (zu generisch oder irrelevant)
    "development": None, "entwicklung": None, "web-development": None,
    "web-entwicklung": None, "app-entwicklung": None,
    "projekt-management": None, "projekt-planung": None,
    "projekt-setup": None, "projektsetup": None, "projekt-verwaltung": None,
    "projekt-analyse": None, "organisationsstruktur": None, "organisatorisches": None,
    "pdf-verarbeitung": None, "pdf-processing": None, "text-processing": None,
    "text-verarbeitung": None, "directus": None,
    "open-source": None, "versionskontrolle": None,
    "suche": None, "datei-suche": None,
    "i18n": None, "routing": None, "react": None, "state-management": None,
    "rendering": None, "initialisierung": None, "konfiguration": None,
    "limitations": None, "analyse": None, "anforderungen": None,
    "anfragen-verhalten": None, "feature-implementation": None,
    "development-workflow": None, "kostenkontrolle": None,
    "kostenverwaltung": None, "resource-management": None,
    "workspace-management": None, "repository": None, "repository-struktur": None,
    "integration": None, "automatisierung": None, "3d-grafik": None,
    "daten-analyse": None,
}


def normalize_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Map raw tags to curated set. Returns (tags, projects) separately."""
    tag_result = set()
    project_result = set()
    for tag in tags:
        tag = tag.lower().strip()
        if tag in TAG_MIGRATION:
            mapped = TAG_MIGRATION[tag]
            if mapped:
                if mapped in PROJECTS:
                    project_result.add(mapped)
                else:
                    tag_result.add(mapped)
        elif tag in PROJECTS:
            project_result.add(tag)
        elif tag in ALLOWED_TAGS:
            tag_result.add(tag)
        else:
            # Unknown — keep as tag (the set can grow if it makes sense)
            tag_result.add(tag)
    return sorted(tag_result), sorted(project_result)

# Chroma connection (same server as Eli's memories)
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
COLLECTION_NAME = "session_archive_v2"

chroma_collection = None

# Neo4j connection
NEO4J_URI = os.environ.get("NEO4J_URI", "")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
neo4j_driver = None


def init_neo4j():
    """Connect to Neo4j (schema v2 — knowledge graph, no session sync)."""
    global neo4j_driver
    if not NEO4J_URI:
        print("  Neo4j: Kein NEO4J_URI konfiguriert")
        return
    try:
        from neo4j import GraphDatabase
        neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        neo4j_driver.verify_connectivity()
        # Constraints for knowledge graph schema v2
        with neo4j_driver.session() as session:
            for label in ["Person", "Projekt", "Thema", "Organisation"]:
                try:
                    session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE")
                except Exception:
                    pass
        # Count nodes for info
        with neo4j_driver.session() as session:
            r = session.run("MATCH (n) RETURN count(n) as c").single()
            count = r["c"] if r else 0
        print(f"  Neo4j: Verbunden mit {NEO4J_URI} ({count} Knoten)")
    except Exception as e:
        print(f"  Neo4j nicht verfügbar: {e}")
        neo4j_driver = None


def sync_to_neo4j(db):
    """DEACTIVATED: Knowledge graph v2 is populated via neo4j_import.py script.
    Sessions are no longer nodes in the graph — they are source references (properties)."""
    return  # Disabled: graph is managed externally via import script

    # --- OLD CODE (v1 schema with Session nodes) ---
    if not neo4j_driver:
        return

    with db_lock:
        sessions = db.execute(
            "SELECT id, title, first_ts, last_ts, msg_count, user_id, summary, tags, graph_data FROM sessions ORDER BY last_ts"
        ).fetchall()

    print(f"  Neo4j sync: {len(sessions)} Sessions...")
    session_ids = {s[0] for s in sessions}

    with neo4j_driver.session() as neo_session:
        # Remove sessions from Neo4j that are no longer in SQLite (e.g. warm-ups)
        neo_sids = [r["id"] for r in neo_session.run("MATCH (s:Session) RETURN s.id as id").data()]
        stale = [sid for sid in neo_sids if sid not in session_ids]
        if stale:
            neo_session.run("MATCH (s:Session) WHERE s.id IN $ids DETACH DELETE s", ids=stale)
            print(f"  Neo4j: {len(stale)} veraltete Sessions entfernt")

        # Create constraints for node types
        for label in ["Project", "Question"]:
            try:
                neo_session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE")
            except Exception:
                pass

        # 1. Upsert all sessions as nodes
        for s in sessions:
            sid, title, first_ts, last_ts, msg_count, user_id, summary, tags_json, graph_data_json = s
            neo_session.run("""
                MERGE (s:Session {id: $id})
                SET s.summary = $summary, s.user_id = $user_id,
                    s.first_ts = $first_ts, s.last_ts = $last_ts,
                    s.msg_count = $msg_count, s.title = $title
            """, id=sid, summary=summary or title[:120], user_id=user_id,
                first_ts=first_ts, last_ts=last_ts, msg_count=msg_count,
                title=title[:120])

            # 2. Person node + :BY edge
            neo_session.run("""
                MERGE (p:Person {name: $name})
                WITH p
                MATCH (s:Session {id: $sid})
                MERGE (s)-[:BY]->(p)
            """, name=user_id or "anton", sid=sid)

            # 3. Tag nodes + :TAGGED edges
            if tags_json:
                try:
                    tags = json.loads(tags_json)
                    for tag in tags:
                        neo_session.run("""
                            MERGE (t:Tag {name: $name})
                            WITH t
                            MATCH (s:Session {id: $sid})
                            MERGE (s)-[:TAGGED]->(t)
                        """, name=tag, sid=sid)
                except (json.JSONDecodeError, TypeError):
                    pass

            # 3b. Graph data: Projects, Mentions
            if graph_data_json:
                try:
                    gd = json.loads(graph_data_json)

                    # Project nodes + :BELONGS_TO edges
                    for project in gd.get("projects", []):
                        pname = project.lower().strip()
                        if pname in PROJECTS:
                            neo_session.run("""
                                MERGE (p:Project {name: $name})
                                WITH p
                                MATCH (s:Session {id: $sid})
                                MERGE (s)-[:BELONGS_TO]->(p)
                            """, name=pname, sid=sid)

                    # Question nodes are curated manually (not auto-generated)

                    # :MENTIONS edges to existing Person nodes
                    for person in gd.get("mentions", []):
                        neo_session.run("""
                            MERGE (p:Person {name: $name})
                            WITH p
                            MATCH (s:Session {id: $sid})
                            MERGE (s)-[:MENTIONS]->(p)
                        """, name=person.lower().strip(), sid=sid)

                except (json.JSONDecodeError, TypeError):
                    pass

        # 4. :FOLLOWS edges (temporal order per user)
        for user_id in set(s[5] for s in sessions):
            user_sessions = [s for s in sessions if s[5] == user_id and s[3]]  # has last_ts
            user_sessions.sort(key=lambda x: x[3] or "")
            for i in range(len(user_sessions) - 1):
                neo_session.run("""
                    MATCH (a:Session {id: $from_id})
                    MATCH (b:Session {id: $to_id})
                    MERGE (a)-[:FOLLOWS]->(b)
                """, from_id=user_sessions[i][0], to_id=user_sessions[i+1][0])

        # 5. :SIMILAR edges from Chroma embeddings (Top-3 per session)
        if chroma_collection:
            # Get all session summary embeddings
            for s in sessions:
                sid = s[0]
                try:
                    results = chroma_collection.query(
                        query_texts=[s[6] or s[1][:120]],  # summary or title
                        n_results=4,
                        where={"role": "summary"},
                        include=["metadatas", "distances"],
                    )
                    if results["ids"][0]:
                        for i, doc_id in enumerate(results["ids"][0]):
                            other_sid = results["metadatas"][0][i]["session_id"]
                            dist = results["distances"][0][i]
                            if other_sid != sid and dist < 1.5:  # threshold
                                neo_session.run("""
                                    MATCH (a:Session {id: $from_id})
                                    MATCH (b:Session {id: $to_id})
                                    MERGE (a)-[r:SIMILAR]->(b)
                                    SET r.distance = $dist
                                """, from_id=sid, to_id=other_sid, dist=round(dist, 3))
                except Exception:
                    pass  # skip if embedding not found

        # Count results
        result = neo_session.run("MATCH (n) RETURN count(n) as nodes")
        node_count = result.single()["nodes"]
        result = neo_session.run("MATCH ()-[r]->() RETURN count(r) as rels")
        rel_count = result.single()["rels"]
        print(f"  Neo4j sync fertig: {node_count} Knoten, {rel_count} Kanten")


def _build_session_id_map(db):
    """Build a mapping from 8-char short IDs to full UUIDs."""
    rows = db.execute("SELECT id FROM sessions").fetchall()
    return {r[0][:8]: r[0] for r in rows}


def get_graph_data():
    """Get nodes + edges from Neo4j for D3 visualization (schema v2 — no Session nodes)."""
    if not neo4j_driver:
        return {"nodes": [], "links": []}

    KNOWN_LABELS = {
        "Person", "Projekt", "Thema", "Organisation",
        "Aufgabe", "Erkenntnis", "Entscheidung", "Meilenstein",
        "Herausforderung", "Spannung", "Artefakt"
    }

    nodes = []
    links = []

    # Build short_id → full UUID map from SQLite
    with db_lock:
        _tmp_db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        sid_map = _build_session_id_map(_tmp_db)
        _tmp_db.close()

    with neo4j_driver.session() as session:
        result = session.run("""
            MATCH (n)
            WHERE any(l IN labels(n) WHERE l IN [
                'Person','Projekt','Thema','Organisation',
                'Aufgabe','Erkenntnis','Entscheidung','Meilenstein',
                'Herausforderung','Spannung','Artefakt','Frage'
            ])
            RETURN id(n) as neo_id, labels(n) as labels, properties(n) as props
        """)
        node_map = {}
        for record in result:
            neo_id = record["neo_id"]
            labels = record["labels"]
            label = next((l for l in labels if l in KNOWN_LABELS), labels[0])
            props = dict(record["props"])
            idx = len(nodes)
            node_map[neo_id] = idx

            node = {
                "id": idx,
                "neo_id": neo_id,
                "type": label,
                "name": props.get("name") or props.get("text", "?"),
            }
            if props.get("beschreibung"):
                node["beschreibung"] = props["beschreibung"]
            if props.get("status"):
                node["status"] = props["status"]
            if props.get("projekt"):
                node["projekt"] = props["projekt"]
            # session_ids (v2 field name) or legacy sessions field
            raw_sids = props.get("session_ids") or props.get("sessions")
            if raw_sids:
                # Expand 8-char short IDs to full UUIDs
                node["sessions"] = [
                    sid_map.get(s, s) for s in raw_sids
                ]
            if props.get("msg_refs"):
                mr = props["msg_refs"]
                if isinstance(mr, str):
                    try:
                        node["msg_refs"] = json.loads(mr)
                    except Exception:
                        pass
                else:
                    node["msg_refs"] = mr
            if props.get("code_ref"):
                node["code_ref"] = props["code_ref"]
            if props.get("verantwortlich"):
                node["verantwortlich"] = props["verantwortlich"]
            nodes.append(node)

        result = session.run("""
            MATCH (a)-[r]->(b)
            RETURN id(a) as source, id(b) as target, type(r) as rel_type
        """)
        for record in result:
            src = node_map.get(record["source"])
            tgt = node_map.get(record["target"])
            if src is not None and tgt is not None:
                links.append({
                    "source": src,
                    "target": tgt,
                    "type": record["rel_type"],
                })

    return {"nodes": nodes, "links": links}


def get_node_memories(neo_id):
    """Get all Memory nodes connected to a given node, with full text."""
    if not neo4j_driver:
        return []

    memories = []
    with neo4j_driver.session() as session:
        result = session.run("""
            MATCH (n)-[r]-(m:Memory)
            WHERE id(n) = $neo_id
            RETURN m.text as text, m.short as short, m.typ as typ, m.datum as datum,
                   m.source as source, m.thema as thema, m.bedeutung as bedeutung,
                   type(r) as rel_type
            ORDER BY m.datum DESC, m.typ
        """, neo_id=neo_id)
        for record in result:
            memories.append({
                "text": record["text"] or record["short"] or "",
                "typ": record["typ"] or "",
                "datum": record["datum"] or "",
                "source": record["source"] or "",
                "thema": record["thema"] or "",
                "bedeutung": record["bedeutung"] or "",
                "rel": record["rel_type"] or "",
            })
    return memories


def graph_query(query_type: str, params: dict) -> dict:
    """Execute predefined graph queries against Neo4j (schema v2).

    Supported query_type values:
      bridges       — themes shared between sessions (high session count)
      neighbors     — all neighbors of a given node (by name + type)
      path          — shortest path between two nodes
      concepts      — all Thema nodes with session counts
      decisions     — all Entscheidung nodes
      questions     — all Herausforderung/Spannung nodes
      person        — everything connected to a person
      stats         — graph statistics
    """
    if not neo4j_driver:
        return {"error": "Neo4j nicht verfügbar"}

    with neo4j_driver.session() as s:
        if query_type == "bridges":
            result = s.run("""
                MATCH (t:Thema)
                WHERE t.sessions IS NOT NULL AND size(t.sessions) >= 2
                RETURN t.name AS thema, t.sessions AS sessions, size(t.sessions) AS count
                ORDER BY count DESC
                LIMIT 30
            """)
            rows = [{"thema": r["thema"], "sessions": r["sessions"], "count": r["count"]}
                    for r in result]
            return {"query": "bridges", "results": rows}

        elif query_type == "neighbors":
            name = params.get("name", "")
            node_type = params.get("type", "")
            if node_type:
                cypher = f"""
                    MATCH (n:{node_type} {{name: $name}})-[r]-(m)
                    RETURN labels(m)[0] AS type, m.name AS name,
                           type(r) AS rel, m.beschreibung AS beschreibung,
                           m.status AS status
                    ORDER BY type, name
                    LIMIT 50
                """
            else:
                cypher = """
                    MATCH (n {name: $name})-[r]-(m)
                    RETURN labels(m)[0] AS type, m.name AS name,
                           type(r) AS rel, m.beschreibung AS beschreibung,
                           m.status AS status
                    ORDER BY type, name
                    LIMIT 50
                """
            result = s.run(cypher, name=name)
            rows = []
            for r in result:
                row = {"type": r["type"], "name": r["name"], "rel": r["rel"]}
                if r["beschreibung"]:
                    row["beschreibung"] = r["beschreibung"][:150]
                if r["status"]:
                    row["status"] = r["status"]
                rows.append(row)
            return {"query": "neighbors", "node": name, "results": rows}

        elif query_type == "path":
            from_name = params.get("from", "")
            to_name = params.get("to", "")
            result = s.run("""
                MATCH (a {name: $from_name}), (b {name: $to_name}),
                      p = shortestPath((a)-[*..6]-(b))
                RETURN [n IN nodes(p) | {type: labels(n)[0], name: n.name}] AS path,
                       [r IN relationships(p) | type(r)] AS rels
                LIMIT 1
            """, from_name=from_name, to_name=to_name)
            record = result.single()
            if record:
                return {"query": "path", "from": from_name, "to": to_name,
                        "path": record["path"], "rels": record["rels"]}
            return {"query": "path", "from": from_name, "to": to_name, "path": [], "rels": []}

        elif query_type == "concepts":
            result = s.run("""
                MATCH (t:Thema)
                RETURN t.name AS thema,
                       size(coalesce(t.sessions, [])) AS session_count
                ORDER BY session_count DESC
                LIMIT 40
            """)
            rows = [{"thema": r["thema"], "sessions": r["session_count"]} for r in result]
            return {"query": "concepts", "results": rows}

        elif query_type == "decisions":
            result = s.run("""
                MATCH (d:Entscheidung)
                RETURN d.name AS name, d.beschreibung AS beschreibung,
                       d.projekt AS projekt, d.status AS status,
                       size(coalesce(d.sessions, [])) AS session_count
                ORDER BY session_count DESC
                LIMIT 40
            """)
            rows = [{
                "name": r["name"],
                "beschreibung": (r["beschreibung"] or "")[:120],
                "projekt": r["projekt"] or "",
                "status": r["status"] or "",
                "sessions": r["session_count"],
            } for r in result]
            return {"query": "decisions", "results": rows}

        elif query_type == "questions":
            result = s.run("""
                MATCH (n)
                WHERE (n:Herausforderung OR n:Spannung) AND n.status IN ['offen', null, '']
                RETURN labels(n)[0] AS type, n.name AS name,
                       n.projekt AS projekt,
                       size(coalesce(n.sessions, [])) AS session_count
                ORDER BY session_count DESC
                LIMIT 40
            """)
            rows = [{
                "type": r["type"],
                "name": r["name"],
                "projekt": r["projekt"] or "",
                "sessions": r["session_count"],
            } for r in result]
            return {"query": "questions", "results": rows}

        elif query_type == "person":
            name = params.get("name", "")
            result_verant = s.run("""
                MATCH (p:Person {name: $name})<-[:VERANTWORTLICH]-(n)
                RETURN labels(n)[0] AS type, n.name AS name,
                       n.beschreibung AS beschreibung, n.status AS status,
                       n.projekt AS projekt
                ORDER BY type, name
                LIMIT 20
            """, name=name)
            responsible = [{
                "type": r["type"], "name": r["name"],
                "beschreibung": (r["beschreibung"] or "")[:100],
                "status": r["status"] or "",
                "projekt": r["projekt"] or "",
            } for r in result_verant]

            result_proj = s.run("""
                MATCH (p:Person {name: $name})-[:ARBEITET_AN]->(proj:Projekt)
                RETURN proj.name AS projekt
                ORDER BY projekt
            """, name=name)
            projects = [r["projekt"] for r in result_proj]

            result_info = s.run("""
                MATCH (p:Person {name: $name})
                RETURN p.beschreibung AS beschreibung, p.rolle AS rolle
            """, name=name)
            info = result_info.single()
            beschreibung = info["beschreibung"] if info else ""
            rolle = info["rolle"] if info else ""

            return {"query": "person", "name": name,
                    "beschreibung": beschreibung or "",
                    "rolle": rolle or "",
                    "responsible_for": responsible,
                    "projects": projects}

        elif query_type == "stats":
            result = s.run("""
                MATCH (n) RETURN labels(n)[0] AS type, count(n) AS count
                ORDER BY count DESC
            """)
            node_stats = {r["type"]: r["count"] for r in result}

            result = s.run("""
                MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count
                ORDER BY count DESC
            """)
            edge_stats = {r["type"]: r["count"] for r in result}

            return {"query": "stats", "nodes": node_stats, "edges": edge_stats}

        else:
            return {"error": f"Unbekannter query_type: {query_type}",
                    "supported": ["bridges", "neighbors", "path", "concepts", "decisions",
                                  "questions", "person", "stats"]}


def init_chroma(force_rebuild=False):
    """Connect to Chroma and get/create collection."""
    global chroma_collection
    try:
        import chromadb
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        if force_rebuild:
            try:
                client.delete_collection(COLLECTION_NAME)
                print(f"  Chroma: Collection '{COLLECTION_NAME}' gelöscht für Neuaufbau")
            except Exception:
                pass
        chroma_collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"description": "Eli & Anton session archive for semantic search"},
        )
        print(f"  Chroma: {chroma_collection.count()} Dokumente in '{COLLECTION_NAME}'")
    except Exception as e:
        print(f"  Chroma nicht verfügbar: {e}")
        chroma_collection = None


# ── Parsing ─────────────────────────────────────────────

def strip_tags(text: str) -> str:
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ide_selection>.*?</ide_selection>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ide_opened_file>.*?</ide_opened_file>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-name>.*?</command-name>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
    text = re.sub(r'<task-notification>.*?</task-notification>', '', text, flags=re.DOTALL)
    text = re.sub(r'<.*?>.*?</.*?>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[a-z_-]+>.*?</[a-z_-]+>', '', text, flags=re.DOTALL)
    return text.strip()


def extract_text(content) -> str:
    if isinstance(content, str):
        return strip_tags(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(strip_tags(item.get("text", "")))
                elif item.get("type") == "tool_use":
                    name = item.get("name", "")
                    inp = item.get("input", {})
                    if name == "mcp__eli__eli_init":
                        parts.append("*[Eli initialisiert sich]*")
                    elif name == "mcp__eli__eli_memory_search":
                        parts.append(f"*[Eli sucht: {inp.get('query', '')}]*")
                    elif name == "mcp__eli__eli_memory_save":
                        parts.append("*[Eli speichert Erinnerung]*")
                    elif name == "mcp__eli__eli_telegram_send":
                        parts.append(f"*[Eli sendet Telegram an {inp.get('recipient', '')}]*")
        return "\n".join(p for p in parts if p)
    return ""


def parse_ts(ts_raw):
    if isinstance(ts_raw, str):
        try:
            return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif isinstance(ts_raw, (int, float)):
        try:
            return datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    return None


# ── SQLite Index ─────────────────────────────────────────

db_lock = threading.Lock()


def init_db():
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT,
        first_ts TEXT,
        last_ts TEXT,
        msg_count INTEGER,
        file_hash TEXT,
        user_id TEXT DEFAULT 'anton'
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        text TEXT,
        timestamp TEXT,
        user_id TEXT DEFAULT 'anton',
        FOREIGN KEY (session_id) REFERENCES sessions(id)
    )""")
    # FTS5 virtual table for fulltext search
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
        USING fts5(text, role, session_id, content=messages, content_rowid=id)
    """)
    # Migrate: add columns if missing (existing DBs)
    try:
        db.execute("SELECT user_id FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT DEFAULT 'anton'")
    try:
        db.execute("SELECT user_id FROM messages LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE messages ADD COLUMN user_id TEXT DEFAULT 'anton'")
    try:
        db.execute("SELECT summary FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
    try:
        db.execute("SELECT tags FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE sessions ADD COLUMN tags TEXT")  # JSON array as string
    try:
        db.execute("SELECT graph_data FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE sessions ADD COLUMN graph_data TEXT")  # JSON: projects, decisions, mentions
    # Fix HTML entities in titles (e.g. &uuml; → ü)
    for sid, title in db.execute("SELECT id, title FROM sessions WHERE title LIKE '%&%;%'").fetchall():
        db.execute("UPDATE sessions SET title = ? WHERE id = ?", (html_mod.unescape(title), sid))
    db.commit()
    return db


def file_hash(filepath: str) -> str:
    """Fast hash based on file size + mtime."""
    stat = os.stat(filepath)
    return hashlib.md5(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()


def generate_summary_and_tags(messages: list, user_id: str = "anton") -> dict | None:
    """Call Anthropic API (Haiku) to generate summary + tags for a session."""
    if not ANTHROPIC_API_KEY:
        return None

    # Build condensed transcript: sample user messages across the whole session
    # This gives a representative overview of ALL topics, not just start or end
    user_msgs = [m for m in messages if m["role"] in ("user", "human") and len(m["text"].strip()) > 15]

    if len(user_msgs) > 25:
        # Sample evenly: 8 from start, 8 from middle, 8 from end
        n = len(user_msgs)
        mid = n // 2
        sample = user_msgs[:8] + user_msgs[mid-4:mid+4] + user_msgs[-8:]
    else:
        sample = user_msgs

    transcript_parts = []
    char_count = 0
    for msg in sample:
        text = msg["text"][:300]
        part = f"Mensch: {text}"
        if char_count + len(part) > 6000:
            break
        transcript_parts.append(part)
        char_count += len(part)
    transcript = "\n\n".join(transcript_parts)

    prompt = f"""Analysiere diese Auswahl von Nutzer-Nachrichten aus einem Gespräch mit einer KI (Eli).
Die Nachrichten sind gleichmäßig über die gesamte Session verteilt (Anfang, Mitte, Ende).

Gib zurück:
1. "summary": Zusammenfassung in 1-3 Sätzen auf Deutsch. Wichtigste Themen und Ergebnisse.
2. "projects": 1-2 Projekte denen diese Session zugehört. Wähle AUS DIESER LISTE: {', '.join(sorted(PROJECTS))}. Wenn keines passt, leeres Array.
3. "tags": 2-5 übergeordnete Themen-Tags (KEINE Projekte, die sind separat!).
4. "mentions": Alle erwähnten Personen als Liste. Bekannte Namen: anton, timo, eli, sebastian, tillmann, mathias. Nur lowercase Vornamen. Leeres Array wenn keine.

Tag-Regeln:
- Wähle Tags AUS DIESER LISTE (bevorzugt!): {', '.join(sorted(ALLOWED_TAGS))}
- Nur wenn KEINER dieser Tags passt, darfst du einen neuen vorschlagen — aber nur übergeordnete Kategorien, nie spezifische Tools/Libraries
- Kleingeschrieben, Bindestriche statt Leerzeichen
- FALSCH: "ms-sql-zugriff", "html-entities", "forgejo-self-hosted", "nginx-config" (zu spezifisch!)
- FALSCH als Tag: "web-of-trust", "eli", "money-printer" (das sind Projekte, keine Tags!)

Antworte NUR mit validem JSON, kein anderer Text.
Beispiel: {{"summary": "WoT Demo-App Tests aufgesetzt.", "projects": ["web-of-trust"], "tags": ["testing", "kryptographie"], "mentions": ["anton", "timo"]}}

Gespräch:
{transcript}"""

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        text = result["content"][0]["text"].strip()
        # Parse JSON from response (handle markdown wrapping)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        # Normalize: split tags and projects, merge any project-named tags into projects
        all_tags = parsed.get("tags", []) + parsed.get("projects", [])
        tags, projects = normalize_tags(all_tags)
        return {
            "summary": parsed.get("summary", ""),
            "tags": tags,
            "projects": projects,
            "mentions": parsed.get("mentions", []),
        }
    except Exception as e:
        print(f"  Summary-Fehler: {e}")
        return None


def index_sessions(db: sqlite3.Connection):
    """Incrementally index new/changed JSONL files into SQLite + Chroma."""
    with db_lock:
        _index_sessions_locked(db)


def _index_sessions_locked(db: sqlite3.Connection):
    # Collect JSONL files from all user directories, deduplicate by session_id (keep newest)
    file_map = {}  # session_id → (filepath, user_id, mtime)
    for user_id, user_dir in USERS.items():
        if not os.path.isdir(user_dir):
            continue
        for filepath in glob.glob(os.path.join(user_dir, "**", "*.jsonl"), recursive=True):
            sid = os.path.basename(filepath).replace(".jsonl", "")
            mtime = os.path.getmtime(filepath)
            if sid not in file_map or mtime > file_map[sid][2]:
                file_map[sid] = (filepath, user_id, mtime)
    all_files = [(fp, uid) for fp, uid, _ in file_map.values()]

    existing = {row[0]: row[1] for row in db.execute("SELECT id, file_hash FROM sessions").fetchall()}

    # Check if Chroma needs a full rebuild (connected but empty while SQLite has data)
    chroma_needs_rebuild = (chroma_collection is not None and chroma_collection.count() == 0 and len(existing) > 0)

    new_count = 0
    updated_count = 0
    chroma_docs = []
    chroma_ids = []
    chroma_metas = []

    for filepath, user_id in all_files:
        session_id = os.path.basename(filepath).replace(".jsonl", "")
        fhash = file_hash(filepath)

        sqlite_unchanged = session_id in existing and existing[session_id] == fhash
        if sqlite_unchanged and not chroma_needs_rebuild:
            continue  # unchanged

        # Parse the session
        messages = []
        first_ts = None
        last_ts = None

        with open(filepath) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = obj.get("type", "")
                if msg_type not in ("user", "assistant", "summary"):
                    continue
                msg = obj.get("message", {})
                content = msg.get("content", "")
                ts = parse_ts(msg.get("createdAt") or obj.get("timestamp"))
                if ts and not first_ts:
                    first_ts = ts
                if ts:
                    last_ts = ts
                text = extract_text(content)
                if not text:
                    continue
                if msg_type == "summary":
                    role = "system"
                    text = f"--- Session kompaktiert ---\n{text[:500]}"
                else:
                    role = msg.get("role", msg_type)
                messages.append({"role": role, "text": text, "timestamp": ts})

        if len(messages) < MIN_MSG_COUNT:
            continue

        # Title = first meaningful user message
        title = ""
        for m in messages:
            if m["role"] in ("user", "human"):
                clean = m["text"].strip()
                if clean and len(clean) > 3:
                    title = html_mod.unescape(clean[:120])
                    break
        if not title:
            title = f"Session {session_id[:8]}"

        # Update SQLite only if data actually changed
        if not sqlite_unchanged:
            # Delete old data if updating
            if session_id in existing:
                db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                updated_count += 1
            else:
                new_count += 1

            # Insert session
            db.execute(
                "INSERT INTO sessions (id, title, first_ts, last_ts, msg_count, file_hash, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, title,
                 first_ts.isoformat() if first_ts else None,
                 last_ts.isoformat() if last_ts else None,
                 len(messages), fhash, user_id),
            )

            # Insert messages
            for msg in messages:
                ts_str = msg["timestamp"].isoformat() if msg["timestamp"] else None
                cur = db.execute(
                    "INSERT INTO messages (session_id, role, text, timestamp, user_id) VALUES (?, ?, ?, ?, ?)",
                    (session_id, msg["role"], msg["text"], ts_str, user_id),
                )
                # FTS5 sync
                db.execute(
                    "INSERT INTO messages_fts (rowid, text, role, session_id) VALUES (?, ?, ?, ?)",
                    (cur.lastrowid, msg["text"], msg["role"], session_id),
                )

        # Prepare Chroma documents — full text with context
        date_str = first_ts.strftime("%Y-%m-%d") if first_ts else "?"
        for i, msg in enumerate(messages):
            if len(msg["text"]) < 20:
                continue  # skip very short messages
            doc_id = f"{session_id}:{i}"

            # Build enriched text for embedding:
            # 1. Context: include previous message so answers know their question
            context_prefix = ""
            if i > 0 and messages[i-1]["text"].strip():
                prev = messages[i-1]
                prev_text = prev["text"][:300]
                context_prefix = f"[Kontext: {prev['role']} sagte: {prev_text}]\n\n"

            # 2. Metadata prefix for better semantic matching
            role_label = "Mensch" if msg["role"] in ("user", "human") else "Eli"
            meta_prefix = f"[{role_label}, {date_str}, Session: {title[:60]}]\n"

            # 3. Full text (no truncation)
            text_for_embedding = meta_prefix + context_prefix + msg["text"]

            chroma_docs.append(text_for_embedding)
            chroma_ids.append(doc_id)
            chroma_metas.append({
                "session_id": session_id,
                "user_id": user_id,
                "role": msg["role"],
                "date": date_str,
                "title": title[:80],
            })

        # Session summary document for high-level semantic search
        if len(messages) >= 4:
            # Build summary from title + all user messages (condensed)
            user_msgs = [m["text"][:200] for m in messages if m["role"] in ("user", "human") and len(m["text"]) > 10]
            assistant_msgs = [m["text"][:200] for m in messages if m["role"] == "assistant" and len(m["text"]) > 50]
            summary_parts = [f"Session: {title}",  f"Datum: {date_str}", f"Nutzer: {user_id}"]
            if user_msgs:
                summary_parts.append("Themen (Mensch): " + " | ".join(user_msgs[:15]))
            if assistant_msgs:
                summary_parts.append("Antworten (Eli): " + " | ".join(assistant_msgs[:10]))
            summary_text = "\n".join(summary_parts)

            chroma_docs.append(summary_text)
            chroma_ids.append(f"{session_id}:summary")
            chroma_metas.append({
                "session_id": session_id,
                "user_id": user_id,
                "role": "summary",
                "date": date_str,
                "title": title[:80],
            })

    # Remove sessions below MIN_MSG_COUNT (warm-up/test sessions)
    warmup_ids = [r[0] for r in db.execute(
        "SELECT id FROM sessions WHERE msg_count < ?", (MIN_MSG_COUNT,)).fetchall()]
    if warmup_ids:
        placeholders = ",".join("?" * len(warmup_ids))
        db.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", warmup_ids)
        db.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", warmup_ids)
        print(f"  Warm-up Sessions entfernt: {len(warmup_ids)} (< {MIN_MSG_COUNT} Nachrichten)")
        # Remove from Chroma
        if chroma_collection:
            chroma_del_ids = []
            for sid in warmup_ids:
                chroma_del_ids.append(f"{sid}:summary")
                # Message chunks use {sid}:{index}
                for i in range(50):
                    chroma_del_ids.append(f"{sid}:{i}")
            try:
                chroma_collection.delete(ids=chroma_del_ids)
            except Exception:
                pass  # IDs that don't exist are silently ignored

    db.commit()

    # Batch upsert to Chroma
    if chroma_collection and chroma_docs:
        # Chroma has a batch limit of ~5000
        batch_size = 4000
        for i in range(0, len(chroma_docs), batch_size):
            chroma_collection.upsert(
                ids=chroma_ids[i:i + batch_size],
                documents=chroma_docs[i:i + batch_size],
                metadatas=chroma_metas[i:i + batch_size],
            )

    if new_count or updated_count:
        total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        total_msgs = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        print(f"  Index: {new_count} neu, {updated_count} aktualisiert → {total} Sessions, {total_msgs} Nachrichten")
    if chroma_collection and chroma_docs:
        print(f"  Chroma: {chroma_collection.count()} Dokumente ({len(chroma_docs)} upserted)")


# ── Query helpers ────────────────────────────────────────

def search_fts(db, query: str, limit=50, user_id=None):
    """Fulltext search using SQLite FTS5 with prefix matching."""
    fts_query = re.sub(r'[^\w\s]', '', query)
    fts_terms = fts_query.split()
    if not fts_terms:
        return []
    # Prefix-match all terms (Till* matches Tillmann)
    fts_expr = " AND ".join(f'"{t}"*' for t in fts_terms)

    # msg_idx: 0-based position of message within its session
    idx_sql = "(SELECT COUNT(*) FROM messages m2 WHERE m2.session_id = m.session_id AND m2.id < m.id)"
    if user_id:
        with db_lock:
            rows = db.execute(f"""
                SELECT m.session_id, m.role, m.text, m.timestamp, s.title, s.first_ts,
                       snippet(messages_fts, 0, '<mark>', '</mark>', '...', 30) as snippet,
                       m.user_id, {idx_sql} as msg_idx
                FROM messages_fts f
                JOIN messages m ON m.id = f.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ? AND m.user_id = ?
                ORDER BY rank
                LIMIT ?
            """, (fts_expr, user_id, limit)).fetchall()
    else:
        with db_lock:
            rows = db.execute(f"""
                SELECT m.session_id, m.role, m.text, m.timestamp, s.title, s.first_ts,
                       snippet(messages_fts, 0, '<mark>', '</mark>', '...', 30) as snippet,
                       m.user_id, {idx_sql} as msg_idx
                FROM messages_fts f
                JOIN messages m ON m.id = f.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_expr, limit)).fetchall()
    return rows


def search_semantic(query: str, limit=50, user_id=None):
    """Semantic search using Chroma embeddings."""
    if not chroma_collection:
        return []
    where_filter = {"user_id": user_id} if user_id else None
    results = chroma_collection.query(
        query_texts=[query],
        n_results=limit,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )
    if not results["ids"][0]:
        return []
    out = []
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        doc = results["documents"][0][i]
        dist = results["distances"][0][i]

        # Strip enrichment prefixes to return clean text
        text = doc
        # Remove [Mensch/Eli, ...] prefix and [Kontext: ...] block
        if text.startswith("["):
            # Skip metadata line
            nl = text.find("\n")
            if nl > 0:
                text = text[nl+1:]
            # Skip context block if present
            if text.startswith("[Kontext:"):
                nl2 = text.find("]\n")
                if nl2 > 0:
                    text = text[nl2+2:]
                    if text.startswith("\n"):
                        text = text[1:]

        # Extract msg index from doc_id (format: "session_id:N" or "session_id:summary")
        parts = doc_id.split(":")
        msg_idx = int(parts[-1]) if len(parts) >= 2 and parts[-1].isdigit() else None

        out.append({
            "session_id": meta["session_id"],
            "user_id": meta.get("user_id", "anton"),
            "role": meta["role"],
            "date": meta["date"],
            "title": meta["title"],
            "text": text,
            "distance": dist,
            "msg_idx": msg_idx,
        })
    return out


def get_all_sessions_from_db(db, user_id=None):
    with db_lock:
        if user_id:
            rows = db.execute(
                "SELECT id, title, first_ts, last_ts, msg_count, user_id, summary, tags FROM sessions WHERE user_id = ? ORDER BY last_ts DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, title, first_ts, last_ts, msg_count, user_id, summary, tags FROM sessions ORDER BY last_ts DESC"
            ).fetchall()
    return [{"id": r[0], "title": r[1], "first_ts": r[2], "last_ts": r[3], "msg_count": r[4], "user_id": r[5], "summary": r[6], "tags": r[7]} for r in rows]


def get_session_messages(db, session_id: str):
    with db_lock:
        rows = db.execute(
            "SELECT role, text, timestamp FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [{"role": r[0], "text": r[1], "timestamp": r[2]} for r in rows]


# ── Markdown → HTML ──────────────────────────────────────

import markdown as _md
_md_instance = _md.Markdown(extensions=["tables", "fenced_code", "nl2br"])


def md_to_html(text: str) -> str:
    _md_instance.reset()
    return _md_instance.convert(text)


# ── HTML Templates ───────────────────────────────────────

STYLE = """
<style>
  /* ── Reset & Base ─────────────────────────────── */
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --bg:        #1e1e1e;
    --bg-raised: #252526;
    --bg-hover:  #2a2d2e;
    --bg-input:  #3c3c3c;
    --border:    #3c3c3c;
    --border-hl: #007acc;
    --fg:        #cccccc;
    --fg-dim:    #858585;
    --fg-bright: #d4d4d4;
    --fg-white:  #e8e8e8;
    --accent:    #007acc;
    --accent2:   #c586c0;
    --blue:      #569cd6;
    --green:     #6a9955;
    --orange:    #ce9178;
    --yellow:    #dcdcaa;
    --red:       #f44747;
    --mono: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'SF Mono', 'Consolas', 'Monaco', monospace;
    --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
  }

  body {
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.65;
    background: var(--bg);
    color: var(--fg);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }

  .container {
    max-width: 820px;
    margin: 0 auto;
    padding: 2.5rem 2rem;
  }

  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* ── Header ───────────────────────────────────── */
  h1 {
    font-size: 1.4rem;
    font-weight: 600;
    color: var(--fg-white);
    margin-bottom: 0.2rem;
    letter-spacing: -0.01em;
  }

  .subtitle {
    color: var(--fg-dim);
    font-size: 0.8rem;
    margin-bottom: 2rem;
  }

  /* ── Stats ────────────────────────────────────── */
  .stats {
    display: flex;
    gap: 1px;
    margin-bottom: 1.5rem;
    background: var(--border);
    border-radius: 6px;
    overflow: hidden;
  }

  .stat {
    flex: 1;
    background: var(--bg-raised);
    padding: 0.8rem 1rem;
    text-align: center;
  }

  .stat-num {
    font-size: 1.3rem;
    font-weight: 600;
    color: var(--fg-white);
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }

  .stat-label {
    font-size: 0.7rem;
    color: var(--fg-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 0.15rem;
  }

  /* ── Search ───────────────────────────────────── */
  .search-form {
    display: flex;
    gap: 0;
    margin-bottom: 1.5rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    transition: border-color 0.15s;
  }

  .search-form:focus-within { border-color: var(--border-hl); }

  .search-box {
    flex: 1;
    padding: 0.6rem 0.9rem;
    background: var(--bg-input);
    border: none;
    color: var(--fg-bright);
    font-family: var(--sans);
    font-size: 0.85rem;
    outline: none;
  }

  .search-box::placeholder { color: #6a6a6a; }

  .search-toggle {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    color: var(--fg-dim);
    font-size: 0.75rem;
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
    padding: 0 0.8rem;
    background: var(--bg-raised);
    border-left: 1px solid var(--border);
    transition: all 0.15s;
  }

  .search-toggle:hover { color: var(--fg); }
  .search-toggle:has(input:checked) { color: var(--accent); }
  .search-toggle input { accent-color: var(--accent); }

  /* ── Session Table ────────────────────────────── */
  #session-table { width: 100%; border-collapse: collapse; }

  #session-table th {
    text-align: left;
    padding: 0.5rem 0.75rem;
    color: var(--fg-dim);
    font-size: 0.7rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    border-bottom: 1px solid var(--border);
  }

  #session-table td {
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid #2a2a2a;
    font-size: 0.85rem;
  }

  #session-table tr:hover { background: var(--bg-hover); }

  #session-table a {
    color: var(--fg-bright);
    transition: color 0.1s;
  }

  #session-table a:hover {
    color: var(--accent);
    text-decoration: none;
  }

  .user-col {
    width: 0.8rem;
    padding-right: 0 !important;
    padding-left: 0.5rem !important;
    vertical-align: baseline;
  }
  .date {
    white-space: nowrap;
    color: var(--fg-dim);
    font-family: var(--mono);
    font-size: 0.75rem;
    vertical-align: baseline;
  }

  .time { color: #555; font-size: 0.7rem; }
  .num { text-align: right; color: var(--fg-dim); font-family: var(--mono); font-size: 0.8rem; }

  /* ── Session Detail ───────────────────────────── */
  .back {
    margin-bottom: 1.5rem;
    font-size: 0.8rem;
  }

  .back a { color: var(--fg-dim); }
  .back a:hover { color: var(--accent); }

  .session-meta {
    color: var(--fg-dim);
    font-size: 0.75rem;
    font-family: var(--mono);
    margin-bottom: 2rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
  }

  /* ── Messages ──────────────────────────────────── */
  .msg {
    margin-bottom: 0.25rem;
    padding: 0.9rem 1rem 0.9rem 1.6rem;
    border-radius: 4px;
    position: relative;
  }

  .msg::before {
    content: '';
    position: absolute;
    left: 0.35rem;
    top: 1.55rem;
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }

  .msg-anton { background: var(--bg-raised); }
  .msg-anton::before { background: var(--accent); }

  .msg-eli { background: transparent; }
  .msg-eli::before { background: var(--accent2); }

  .msg-system { background: transparent; opacity: 0.6; }
  .msg-system::before { background: #555; }

  .msg-time {
    display: block;
    text-align: right;
    font-weight: 400;
    color: #555;
    font-size: 0.65rem;
    font-family: var(--mono);
    margin-top: 0.3rem;
  }

  /* ── Message Body (Markdown) ──────────────────── */
  .msg-body {
    font-size: 0.85rem;
    line-height: 1.7;
    color: var(--fg-bright);
  }

  .msg-body p { margin-bottom: 0.6rem; }
  .msg-body p:last-child { margin-bottom: 0; }

  .msg-body ul, .msg-body ol {
    margin: 0.4rem 0 0.6rem 1.5rem;
  }

  .msg-body li { margin-bottom: 0.2rem; }

  .msg-body blockquote {
    border-left: 2px solid var(--border);
    padding-left: 0.8rem;
    color: var(--fg-dim);
    margin: 0.5rem 0;
  }

  .msg-body pre {
    background: #1a1a1a;
    border: 1px solid #333;
    padding: 0.75rem 1rem;
    border-radius: 4px;
    overflow-x: auto;
    margin: 0.6rem 0;
    font-size: 0.8rem;
    line-height: 1.5;
  }

  .msg-body code {
    font-family: var(--mono);
    background: rgba(255,255,255,0.06);
    padding: 0.15rem 0.35rem;
    border-radius: 3px;
    font-size: 0.82em;
    color: var(--orange);
  }

  .msg-body pre code {
    background: none;
    padding: 0;
    color: var(--fg-bright);
    font-size: inherit;
  }

  .msg-body h1, .msg-body h2, .msg-body h3, .msg-body h4 {
    color: var(--fg-white);
    margin: 1rem 0 0.4rem;
    font-weight: 600;
  }

  .msg-body h1 { font-size: 1.15rem; }
  .msg-body h2 { font-size: 1.05rem; }
  .msg-body h3 { font-size: 0.95rem; }
  .msg-body h4 { font-size: 0.9rem; color: var(--fg); }

  .msg-body strong { color: var(--fg-white); font-weight: 600; }
  .msg-body em { color: var(--fg); font-style: italic; }

  .msg-body a { color: var(--accent); }
  .msg-body a:hover { text-decoration: underline; }

  /* ── Tables in messages & results ─────────────── */
  .msg-body table, .result-text table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.6rem 0;
    font-size: 0.82rem;
  }

  .msg-body th, .result-text th {
    text-align: left;
    padding: 0.4rem 0.6rem;
    background: rgba(255,255,255,0.03);
    border-bottom: 1px solid var(--border);
    color: var(--fg-dim);
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .msg-body td, .result-text td {
    padding: 0.35rem 0.6rem;
    border-bottom: 1px solid #2a2a2a;
  }

  .msg-body tr:hover, .result-text tr:hover { background: var(--bg-hover); }

  /* ── Search Results ───────────────────────────── */
  .search-results { margin-bottom: 1.5rem; }

  .search-result {
    display: block;
    background: var(--bg-raised);
    padding: 0.8rem 1rem;
    border-radius: 4px;
    margin-bottom: 2px;
    border: 1px solid transparent;
    transition: all 0.1s;
  }

  .search-result:hover {
    border-color: var(--border);
    background: var(--bg-hover);
    text-decoration: none;
  }

  .search-result .result-session {
    font-size: 0.7rem;
    color: var(--fg-dim);
    font-family: var(--mono);
  }

  .search-result .result-text {
    margin-top: 0.25rem;
    font-size: 0.83rem;
    color: var(--fg-bright);
    line-height: 1.55;
  }

  .search-result .result-text p { margin-bottom: 0.2rem; }
  .search-result .result-text p:last-child { margin-bottom: 0; }

  .search-result .result-distance {
    font-size: 0.65rem;
    color: #555;
    margin-top: 0.25rem;
    font-family: var(--mono);
  }

  mark {
    background: rgba(255, 204, 0, 0.22);
    color: #e8c96a;
    padding: 0.05rem 0.15rem;
    border-radius: 2px;
  }

  .sem-badge {
    display: inline-block;
    font-size: 0.6rem;
    font-family: var(--mono);
    padding: 0.1rem 0.4rem;
    background: rgba(197, 134, 192, 0.12);
    color: var(--accent2);
    border-radius: 3px;
    margin-left: 0.4rem;
    vertical-align: middle;
  }

  .tag {
    display: inline-block;
    font-size: 0.65rem;
    font-family: var(--mono);
    padding: 0.1rem 0.4rem;
    background: rgba(86, 156, 214, 0.12);
    color: var(--accent);
    border-radius: 3px;
    margin: 0.1rem 0.2rem 0.1rem 0;
    text-decoration: none;
    transition: background 0.15s;
  }
  .tag:hover { background: rgba(86, 156, 214, 0.25); }
  .session-tags { display: block; margin-top: 0.25rem; }

  /* ── Scrollbar ────────────────────────────────── */
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #424242; border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: #555; }
</style>
"""


def _user_badge(user_id):
    """Small colored badge for user identification."""
    colors = {"anton": "var(--accent)", "timo": "#e5a33d"}
    color = colors.get(user_id, "var(--fg-dim)")
    initial = (user_id or "?")[0].upper()
    return f'<span style="display:inline-flex;align-items:center;justify-content:center;width:1rem;height:1rem;border-radius:50%;background:{color};color:#fff;font-size:0.55rem;font-weight:700;line-height:1;position:relative;top:-2px;">{initial}</span>'


def render_graph_page():
    """Render the D3.js force-directed graph visualization page (schema v2)."""
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wissensgraph — Eli</title>
<style>
  *, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
  :root {{
    --bg: #1e1e1e; --bg-raised: #252526; --border: #3c3c3c;
    --fg: #cccccc; --fg-dim: #858585; --fg-white: #e8e8e8;
    --accent: #007acc; --mono: 'Cascadia Code', 'Fira Code', monospace;
    --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }}
  body {{ font-family: var(--sans); background: var(--bg); color: var(--fg); overflow: hidden; }}
  #graph {{ width: 100vw; height: 100vh; }}
  svg {{ width: 100%; height: 100%; }}

  .controls {{
    position: fixed; top: 1rem; left: 1rem; z-index: 10;
    display: flex; flex-direction: column; gap: 0.4rem; max-width: 220px;
  }}
  .controls a, .controls button {{
    background: var(--bg-raised); border: 1px solid var(--border);
    color: var(--fg); padding: 0.3rem 0.6rem; border-radius: 4px;
    font-size: 0.7rem; cursor: pointer; text-decoration: none;
    font-family: var(--sans);
  }}
  .controls button:hover, .controls a:hover {{ border-color: var(--accent); color: var(--fg-white); }}
  .controls button.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .filter-row {{ display: flex; gap: 0.25rem; flex-wrap: wrap; }}
  .filter-label {{ font-size: 0.6rem; color: var(--fg-dim); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 0.2rem; }}

  .detail-panel {{
    position: fixed; top: 0; right: 0; width: 380px; height: 100vh;
    background: var(--bg-raised); border-left: 1px solid var(--border);
    z-index: 20; overflow-y: auto; display: none; padding: 1.2rem;
  }}
  .detail-panel.open {{ display: block; }}
  .detail-close {{
    position: absolute; top: 0.8rem; right: 0.8rem;
    background: none; border: none; color: var(--fg-dim); font-size: 1.2rem;
    cursor: pointer; font-family: var(--sans);
  }}
  .detail-close:hover {{ color: var(--fg-white); }}
  .detail-type {{
    font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 0.15rem 0.5rem; border-radius: 3px; display: inline-block;
    margin-bottom: 0.5rem;
  }}
  .detail-name {{
    font-size: 0.95rem; font-weight: 600; color: var(--fg-white);
    line-height: 1.4; margin-bottom: 0.6rem;
  }}
  .detail-desc {{
    font-size: 0.78rem; color: var(--fg); line-height: 1.5;
    margin-bottom: 0.5rem; padding: 0.5rem 0.6rem;
    background: rgba(255,255,255,0.03); border-radius: 4px;
  }}
  .detail-meta {{ font-size: 0.72rem; color: var(--fg-dim); margin-bottom: 0.25rem; }}
  .detail-meta span {{ color: var(--fg); }}
  .detail-section {{
    margin-top: 0.9rem; padding-top: 0.7rem;
    border-top: 1px solid var(--border);
  }}
  .detail-section h3 {{
    font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--fg-dim); margin-bottom: 0.4rem;
  }}
  .detail-link {{
    display: block; padding: 0.3rem 0.5rem; margin: 0.1rem 0;
    border-radius: 4px; font-size: 0.73rem; color: var(--fg);
    text-decoration: none; cursor: pointer; line-height: 1.4;
  }}
  .detail-link:hover {{ background: var(--bg); color: var(--fg-white); text-decoration: none; }}
  .dl-dot {{
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-right: 0.4rem; vertical-align: middle; flex-shrink: 0;
  }}
  .msg-link {{
    display: inline-block; font-size: 0.65rem; font-family: var(--mono);
    color: var(--accent); opacity: 0.7; margin: 0.1rem 0.15rem 0.1rem 0;
    padding: 0.05rem 0.3rem; border: 1px solid rgba(0,122,204,0.3); border-radius: 3px;
    text-decoration: none; white-space: nowrap;
  }}
  .msg-link:hover {{ opacity: 1; border-color: var(--accent); }}
  .code-ref {{
    font-size: 0.65rem; font-family: var(--mono); color: #ce9178;
    background: rgba(206,145,120,0.1); padding: 0.1rem 0.4rem;
    border-radius: 3px; display: inline-block; margin-top: 0.3rem;
    word-break: break-all;
  }}

  .tooltip {{
    position: fixed; pointer-events: none; z-index: 100;
    background: var(--bg-raised); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.6rem 0.8rem; max-width: 380px;
    font-size: 0.75rem; line-height: 1.5; color: var(--fg);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4); display: none;
  }}
  .tooltip .tt-type {{ color: var(--fg-dim); font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .tooltip .tt-name {{ color: var(--fg-white); font-weight: 600; margin: 0.15rem 0; }}
  .tooltip .tt-detail {{ color: var(--fg-dim); font-size: 0.7rem; }}
  .tooltip .tt-hint {{ color: var(--accent); font-size: 0.65rem; margin-top: 0.3rem; }}

  .legend {{
    position: fixed; bottom: 1rem; left: 1rem; z-index: 10;
    background: var(--bg-raised); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.5rem 0.7rem; font-size: 0.62rem;
  }}
  .legend-item {{ display: flex; align-items: center; gap: 0.35rem; margin: 0.12rem 0; }}
  .legend-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}

  .loading {{
    position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
    color: var(--fg-dim); font-size: 0.9rem;
  }}
</style>
</head><body>
<div id="graph"></div>
<div class="tooltip" id="tooltip"></div>
<div class="detail-panel" id="detail-panel">
  <button class="detail-close" id="detail-close">✕</button>
  <div id="detail-content"></div>
</div>
<div class="loading" id="loading">Wissensgraph wird geladen...</div>

<div class="controls">
  <a href="{BASE_PATH}/">← Archiv</a>
  <div class="filter-label">Knotentyp</div>
  <div class="filter-row">
    <button class="active" data-type="Person">Personen</button>
    <button class="active" data-type="Projekt">Projekte</button>
    <button class="active" data-type="Thema">Themen</button>
    <button class="active" data-type="Organisation">Orgs</button>
  </div>
  <div class="filter-row">
    <button class="active" data-type="Aufgabe">Aufgaben</button>
    <button class="active" data-type="Erkenntnis">Erkenntn.</button>
    <button class="active" data-type="Entscheidung">Entscheid.</button>
    <button class="active" data-type="Meilenstein">Meilens.</button>
  </div>
  <div class="filter-row">
    <button class="active" data-type="Herausforderung">Herausf.</button>
    <button class="active" data-type="Spannung">Spannung.</button>
    <button class="active" data-type="Artefakt">Artefakte</button>
  </div>
  <div class="filter-label">Kanten</div>
  <div class="filter-row">
    <button class="active" data-edge="IN_PROJEKT">Projekt</button>
    <button class="active" data-edge="GEHOERT_ZU">Gehört zu</button>
    <button class="active" data-edge="BETRIFFT">Betrifft</button>
    <button class="active" data-edge="VERANTWORTLICH">Verantw.</button>
    <button class="active" data-edge="VON">Von</button>
    <button class="active" data-edge="HAT_THEMA">Thema</button>
    <button class="active" data-edge="ZWISCHEN">Zwischen</button>
    <button class="active" data-edge="KENNT">Kennt</button>
  </div>
</div>

<div class="legend">
  <div class="legend-item"><div class="legend-dot" style="background:#6a9955"></div> Person</div>
  <div class="legend-item"><div class="legend-dot" style="background:#007acc"></div> Projekt</div>
  <div class="legend-item"><div class="legend-dot" style="background:#569cd6"></div> Thema</div>
  <div class="legend-item"><div class="legend-dot" style="background:#c586c0"></div> Organisation</div>
  <div class="legend-item"><div class="legend-dot" style="background:#dcdcaa"></div> Aufgabe</div>
  <div class="legend-item"><div class="legend-dot" style="background:#4ec9b0"></div> Erkenntnis</div>
  <div class="legend-item"><div class="legend-dot" style="background:#ce9178"></div> Entscheidung</div>
  <div class="legend-item"><div class="legend-dot" style="background:#9cdcfe"></div> Meilenstein</div>
  <div class="legend-item"><div class="legend-dot" style="background:#f44747"></div> Herausforderung</div>
  <div class="legend-item"><div class="legend-dot" style="background:#ff8c00"></div> Spannung</div>
  <div class="legend-item"><div class="legend-dot" style="background:#b5cea8"></div> Artefakt</div>
</div>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function() {{
  const BASE = "{BASE_PATH}";
  const tooltip = document.getElementById('tooltip');
  const loading = document.getElementById('loading');
  const detailPanel = document.getElementById('detail-panel');
  const detailContent = document.getElementById('detail-content');

  document.getElementById('detail-close').addEventListener('click', () => {{
    detailPanel.classList.remove('open');
    if (highlightedNode !== null) {{ highlightedNode = null; updateHighlight(); }}
  }});

  const typeColors = {{
    Person: '#6a9955', Projekt: '#007acc', Thema: '#569cd6',
    Organisation: '#c586c0', Aufgabe: '#dcdcaa', Erkenntnis: '#4ec9b0',
    Entscheidung: '#ce9178', Meilenstein: '#9cdcfe',
    Herausforderung: '#f44747', Spannung: '#ff8c00', Artefakt: '#b5cea8',
  }};
  const typeLabels = {{
    Person: 'Person', Projekt: 'Projekt', Thema: 'Thema',
    Organisation: 'Organisation', Aufgabe: 'Aufgabe', Erkenntnis: 'Erkenntnis',
    Entscheidung: 'Entscheidung', Meilenstein: 'Meilenstein',
    Herausforderung: 'Herausforderung', Spannung: 'Spannung', Artefakt: 'Artefakt',
  }};
  const edgeColors = {{
    IN_PROJEKT: '#007acc44', GEHOERT_ZU: '#569cd644', BETRIFFT: '#c586c044',
    VERANTWORTLICH: '#6a995544', VON: '#ce917844',
    ARBEITET_AN: '#6a995522', HAT_THEMA: '#569cd622',
    ZWISCHEN: '#ff8c0077', KENNT: '#6a995577',
  }};
  const edgeLabels = {{
    IN_PROJEKT: 'in Projekt', GEHOERT_ZU: 'gehört zu', BETRIFFT: 'betrifft',
    VERANTWORTLICH: 'verantwortlich', VON: 'von', ARBEITET_AN: 'arbeitet an',
    HAT_THEMA: 'hat Thema', ZWISCHEN: 'zwischen', KENNT: 'kennt',
  }};

  const typeSizes = {{
    Person: 14, Projekt: 18, Thema: 12, Organisation: 11,
    Aufgabe: 5, Erkenntnis: 6, Entscheidung: 7, Meilenstein: 7,
    Herausforderung: 6, Spannung: 6, Artefakt: 5,
  }};
  const typeCharge = {{
    Person: -400, Projekt: -500, Thema: -300, Organisation: -250,
    Aufgabe: -80, Erkenntnis: -100, Entscheidung: -120, Meilenstein: -100,
    Herausforderung: -90, Spannung: -90, Artefakt: -70,
  }};

  let graphData = null;
  // Multi-select sets: all types active by default
  const ALL_TYPES = new Set(Object.keys(typeColors));
  const ALL_EDGES = new Set(Object.keys(edgeColors));
  let activeTypes = new Set(ALL_TYPES);
  let activeEdges = new Set(ALL_EDGES);
  let highlightedNode = null;
  let currentNodes = [];
  let currentLinks = [];
  let nodeElements, linkElements, labelElements;
  let nodeById = new Map();     // id → node
  let neighborIndex = new Map(); // id to list of {{node, edge, dir}}

  fetch(BASE + '/api/graph')
    .then(r => r.json())
    .then(data => {{
      graphData = data;
      loading.style.display = 'none';
      renderGraph();
    }})
    .catch(err => {{ loading.textContent = 'Fehler: ' + err.message; }});

  function filterData() {{
    if (!graphData) return {{ nodes: [], links: [] }};
    let nodes = graphData.nodes;
    let links = graphData.links;

    if (activeTypes.size < ALL_TYPES.size) {{
      nodes = nodes.filter(n => activeTypes.has(n.type));
      const nodeIds = new Set(nodes.map(n => n.id));
      links = links.filter(l => {{
        const src = typeof l.source === 'object' ? l.source.id : l.source;
        const tgt = typeof l.target === 'object' ? l.target.id : l.target;
        return nodeIds.has(src) && nodeIds.has(tgt);
      }});
    }}

    if (activeEdges.size < ALL_EDGES.size) {{
      // Only filter edges — nodes stay visible regardless of edge filter
      links = links.filter(l => activeEdges.has(l.type));
      // Still remove edges whose endpoints were removed by the node filter
      const nodeIds = new Set(nodes.map(n => n.id));
      links = links.filter(l => {{
        const src = typeof l.source === 'object' ? l.source.id : l.source;
        const tgt = typeof l.target === 'object' ? l.target.id : l.target;
        return nodeIds.has(src) && nodeIds.has(tgt);
      }});
    }}

    return {{ nodes: JSON.parse(JSON.stringify(nodes)), links: JSON.parse(JSON.stringify(links)) }};
  }}

  function getNeighbors(nodeId) {{
    // O(1) lookup via pre-built index (built in renderGraph)
    return neighborIndex.get(nodeId) || [];
  }}

  // msg_refs = [5, 28]  (list of 0-based message indices within the session)
  // sessions  = ['061d22f6', ...]  (list of session IDs)
  function buildMsgRefLinks(msgRefs, sessions) {{
    if (!Array.isArray(msgRefs) || !msgRefs.length) return '';
    if (!sessions || !sessions.length) return '';
    const sid = sessions[0];
    const shortId = sid.substring(0, 8);
    let html = '<div style="margin-top:0.3rem;display:flex;flex-wrap:wrap;gap:2px;">';
    msgRefs.forEach(idx => {{
      html += `<a class="msg-link" href="${{BASE}}/session/${{sid}}#msg-${{idx}}" target="_blank" title="Session ${{shortId}}, Nachricht ${{idx + 1}}">${{shortId}}:${{idx + 1}}</a>`;
    }});
    html += '</div>';
    return html;
  }}

  function showDetail(d) {{
    highlightedNode = d.id;
    updateHighlight();

    const color = typeColors[d.type] || '#666';
    let html = `<div class="detail-type" style="background:${{color}}33;color:${{color}}">${{typeLabels[d.type] || d.type}}</div>`;
    html += `<div class="detail-name">${{escHtml(d.name)}}</div>`;

    if (d.beschreibung) {{
      html += `<div class="detail-desc">${{escHtml(d.beschreibung)}}</div>`;
    }}
    if (d.projekt) {{
      html += `<div class="detail-meta">Projekt: <span>${{escHtml(d.projekt)}}</span></div>`;
    }}
    if (d.status) {{
      const statusColor = d.status === 'erledigt' ? '#6a9955' : d.status === 'offen' ? '#f44747' : '#569cd6';
      html += `<div class="detail-meta">Status: <span style="color:${{statusColor}}">${{escHtml(d.status)}}</span></div>`;
    }}
    if (d.verantwortlich) {{
      html += `<div class="detail-meta">Verantwortlich: <span>${{escHtml(d.verantwortlich)}}</span></div>`;
    }}
    if (d.sessions && d.sessions.length) {{
      html += `<div class="detail-meta">Sessions: <span>${{d.sessions.length}}</span></div>`;
    }}

    if (d.msg_refs && Array.isArray(d.msg_refs) && d.msg_refs.length && d.sessions && d.sessions.length) {{
      html += `<div class="detail-section"><h3>Quell-Nachrichten (${{d.msg_refs.length}})</h3>`;
      html += buildMsgRefLinks(d.msg_refs, d.sessions);
      html += '</div>';
    }} else if (d.sessions && d.sessions.length) {{
      html += '<div class="detail-section"><h3>Quell-Sessions</h3>';
      html += '<div style="margin-top:0.3rem;display:flex;flex-wrap:wrap;gap:2px;">';
      d.sessions.forEach(sid => {{
        const shortId = sid.substring(0, 8);
        html += `<a class="msg-link" href="${{BASE}}/session/${{sid}}" target="_blank">${{shortId}}</a>`;
      }});
      html += '</div></div>';
    }}

    if (d.code_ref) {{
      html += `<div class="detail-section"><h3>Code-Referenz</h3><div class="code-ref">${{escHtml(d.code_ref)}}</div></div>`;
    }}

    const neighbors = getNeighbors(d.id);
    const groups = {{}};
    neighbors.forEach(nb => {{
      const key = nb.node.type;
      if (!groups[key]) groups[key] = [];
      groups[key].push(nb);
    }});

    const typeOrder = ['Person', 'Projekt', 'Thema', 'Organisation',
                       'Erkenntnis', 'Entscheidung', 'Meilenstein',
                       'Aufgabe', 'Herausforderung', 'Spannung', 'Artefakt'];
    typeOrder.forEach(type => {{
      const items = groups[type];
      if (!items || !items.length) return;
      html += `<div class="detail-section"><h3>${{typeLabels[type] || type}} (${{items.length}})</h3>`;
      items.forEach(nb => {{
        const c = typeColors[nb.node.type] || '#666';
        const label = nb.node.name.length > 55 ? nb.node.name.substring(0, 52) + '...' : nb.node.name;
        const edgeHint = edgeLabels[nb.edge] || nb.edge;
        html += `<div class="detail-link" data-focus-id="${{nb.node.id}}"><span class="dl-dot" style="background:${{c}}"></span>${{escHtml(label)}}<span style="font-size:0.6rem;color:var(--fg-dim);"> · ${{edgeHint}}</span></div>`;
      }});
      html += '</div>';
    }});

    detailContent.innerHTML = html;
    detailPanel.classList.add('open');

    detailContent.querySelectorAll('[data-focus-id]').forEach(el => {{
      el.addEventListener('click', () => {{
        const focusId = parseInt(el.dataset.focusId);
        const focusNode = nodeById.get(focusId);
        if (focusNode) showDetail(focusNode);
      }});
    }});
  }}

  function updateHighlight() {{
    if (!nodeElements) return;
    if (highlightedNode === null) {{
      nodeElements.attr('opacity', 1);
      linkElements.attr('opacity', 1);
      labelElements.attr('opacity', 1);
      return;
    }}
    const neighborIds = new Set([highlightedNode]);
    currentLinks.forEach(l => {{
      const src = typeof l.source === 'object' ? l.source.id : l.source;
      const tgt = typeof l.target === 'object' ? l.target.id : l.target;
      if (src === highlightedNode) neighborIds.add(tgt);
      if (tgt === highlightedNode) neighborIds.add(src);
    }});
    nodeElements.attr('opacity', d => neighborIds.has(d.id) ? 1 : 0.1);
    linkElements.attr('opacity', l => {{
      const src = typeof l.source === 'object' ? l.source.id : l.source;
      const tgt = typeof l.target === 'object' ? l.target.id : l.target;
      return (src === highlightedNode || tgt === highlightedNode) ? 0.8 : 0.03;
    }});
    labelElements.attr('opacity', d => neighborIds.has(d.id) ? 1 : 0.05);
  }}

  function renderGraph() {{
    d3.select('#graph svg').remove();
    const data = filterData();
    currentNodes = data.nodes;
    currentLinks = data.links;

    // Index aufbauen für O(1) Neighbor-Lookup
    nodeById = new Map(currentNodes.map(n => [n.id, n]));
    neighborIndex = new Map(currentNodes.map(n => [n.id, []]));
    const seen = new Map();
    currentLinks.forEach(l => {{
      const srcId = typeof l.source === 'object' ? l.source.id : l.source;
      const tgtId = typeof l.target === 'object' ? l.target.id : l.target;
      const keyOut = `${{srcId}}:${{tgtId}}:${{l.type}}`;
      const keyIn  = `${{tgtId}}:${{srcId}}:${{l.type}}`;
      const tgtNode = nodeById.get(tgtId);
      const srcNode = nodeById.get(srcId);
      if (tgtNode && !seen.has(keyOut)) {{
        seen.set(keyOut, true);
        neighborIndex.get(srcId).push({{ node: tgtNode, edge: l.type, dir: 'out' }});
      }}
      if (srcNode && !seen.has(keyIn)) {{
        seen.set(keyIn, true);
        neighborIndex.get(tgtId).push({{ node: srcNode, edge: l.type, dir: 'in' }});
      }}
    }});
    if (!data.nodes.length) {{
      loading.textContent = 'Keine Knoten für diesen Filter.';
      loading.style.display = 'block';
      return;
    }}
    loading.style.display = 'none';

    const panelOpen = detailPanel.classList.contains('open');
    const width = window.innerWidth - (panelOpen ? 380 : 0);
    const height = window.innerHeight;

    const svg = d3.select('#graph').append('svg')
      .attr('viewBox', [0, 0, width, height]);
    const g = svg.append('g');

    const zoom = d3.zoom()
      .scaleExtent([0.05, 10])
      .on('zoom', (e) => g.attr('transform', e.transform));
    svg.call(zoom);

    svg.on('click', (e) => {{
      if (e.target === svg.node()) {{
        highlightedNode = null;
        updateHighlight();
        detailPanel.classList.remove('open');
      }}
    }});

    function nodeRadius(d) {{ return typeSizes[d.type] || 6; }}
    function nodeColor(d) {{ return typeColors[d.type] || '#666'; }}

    const linkDist = {{ IN_PROJEKT: 100, GEHOERT_ZU: 80, BETRIFFT: 70,
                       VERANTWORTLICH: 90, VON: 80, ARBEITET_AN: 120, HAT_THEMA: 70 }};

    const sim = d3.forceSimulation(data.nodes)
      .force('link', d3.forceLink(data.links).id(d => d.id)
        .distance(d => linkDist[d.type] || 80))
      .force('charge', d3.forceManyBody()
        .strength(d => typeCharge[d.type] || -100))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 3))
      .stop();

    // Alle Positionen vorab berechnen
    const n = Math.ceil(Math.log(sim.alphaMin()) / Math.log(1 - sim.alphaDecay()));
    for (let i = 0; i < n; i++) sim.tick();

    // Positionen einfrieren — Sim läuft nie im Hintergrund
    data.nodes.forEach(d => {{ d.fx = d.x; d.fy = d.y; }});

    linkElements = g.append('g')
      .selectAll('line')
      .data(data.links)
      .join('line')
      .attr('stroke', d => edgeColors[d.type] || '#3c3c3c44')
      .attr('stroke-width', 1);

    nodeElements = g.append('g')
      .selectAll('circle')
      .data(data.nodes)
      .join('circle')
      .attr('r', nodeRadius)
      .attr('fill', nodeColor)
      .attr('stroke', '#1e1e1e')
      .attr('stroke-width', 1.5)
      .style('cursor', 'pointer')
      .call(d3.drag()
        .on('start', (e, d) => {{ d.fx = d.x; d.fy = d.y; }})
        .on('drag', (e, d) => {{ d.fx = e.x; d.fy = e.y; ticked(); }})
        .on('end', (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
      );

    const showLabels = activeTypes.size < ALL_TYPES.size || data.nodes.length < 200;
    const labelTypes = new Set(['Person', 'Projekt', 'Thema', 'Organisation',
                                 'Erkenntnis', 'Entscheidung', 'Meilenstein']);
    labelElements = g.append('g')
      .selectAll('text')
      .data(data.nodes.filter(d => showLabels || labelTypes.has(d.type)))
      .join('text')
      .text(d => d.name.length > 30 ? d.name.substring(0, 28) + '…' : d.name)
      .attr('font-size', d => {{
        if (d.type === 'Projekt') return '12px';
        if (['Person', 'Organisation'].includes(d.type)) return '11px';
        if (['Thema', 'Erkenntnis', 'Entscheidung', 'Meilenstein'].includes(d.type)) return '9px';
        return '7px';
      }})
      .attr('fill', d => (typeColors[d.type] || '#999') + 'aa')
      .attr('text-anchor', 'middle')
      .attr('dy', d => nodeRadius(d) + 11)
      .style('pointer-events', 'none')
      .style('font-family', 'var(--sans)');

    nodeElements.on('mouseover', (e, d) => {{
      const neighbors = getNeighbors(d.id);
      let html = '<div class="tt-type">' + (typeLabels[d.type] || d.type) + '</div>';
      html += '<div class="tt-name">' + escHtml(d.name) + '</div>';
      if (d.beschreibung) {{
        html += '<div class="tt-detail" style="margin-top:0.2rem">' + escHtml(d.beschreibung.substring(0, 100)) + (d.beschreibung.length > 100 ? '…' : '') + '</div>';
      }}
      if (d.projekt) html += '<div class="tt-detail">Projekt: ' + escHtml(d.projekt) + '</div>';
      if (d.status) html += '<div class="tt-detail">Status: ' + escHtml(d.status) + '</div>';
      const counts = {{}};
      neighbors.forEach(nb => {{ counts[nb.node.type] = (counts[nb.node.type] || 0) + 1; }});
      const parts = Object.entries(counts).map(([t, c]) => c + ' ' + (typeLabels[t] || t));
      if (parts.length) html += '<div class="tt-detail" style="margin-top:0.2rem">' + parts.join(' · ') + '</div>';
      if (d.sessions && d.sessions.length) html += '<div class="tt-detail">' + d.sessions.length + ' Quell-Sessions</div>';
      html += '<div class="tt-hint">Klicken für Details</div>';
      tooltip.innerHTML = html;
      tooltip.style.display = 'block';
    }})
    .on('mousemove', (e) => {{
      tooltip.style.left = (e.clientX + 14) + 'px';
      tooltip.style.top = Math.min(e.clientY - 10, window.innerHeight - 200) + 'px';
    }})
    .on('mouseout', () => {{ tooltip.style.display = 'none'; }})
    .on('click', (e, d) => {{
      e.stopPropagation();
      tooltip.style.display = 'none';
      showDetail(d);
    }});

    function ticked() {{
      linkElements
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeElements.attr('cx', d => d.x).attr('cy', d => d.y);
      labelElements.attr('x', d => d.x).attr('y', d => d.y);
    }}
    // Einmal rendern mit den vorberechneten Positionen
    ticked();

    // Auto-Fit sofort (Positionen sind bereits berechnet)
    setTimeout(() => {{
      const bounds = g.node().getBBox();
      if (bounds.width > 0) {{
        const scale = Math.min(width / (bounds.width + 120), height / (bounds.height + 120), 1.5) * 0.85;
        const tx = width / 2 - (bounds.x + bounds.width / 2) * scale;
        const ty = height / 2 - (bounds.y + bounds.height / 2) * scale;
        svg.transition().duration(400).call(
          zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale)
        );
      }}
    }}, 100);

    updateHighlight();
  }}

  function escHtml(s) {{
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }}

  document.querySelectorAll('[data-type]').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const t = btn.dataset.type;
      if (activeTypes.has(t)) {{
        activeTypes.delete(t);
        btn.classList.remove('active');
      }} else {{
        activeTypes.add(t);
        btn.classList.add('active');
      }}
      renderGraph();
    }});
  }});

  document.querySelectorAll('[data-edge]').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const e = btn.dataset.edge;
      if (activeEdges.has(e)) {{
        activeEdges.delete(e);
        btn.classList.remove('active');
      }} else {{
        activeEdges.add(e);
        btn.classList.add('active');
      }}
      renderGraph();
    }});
  }});

  window.addEventListener('resize', () => {{ if (graphData) renderGraph(); }});
}})();
</script>
</body></html>"""

def render_index(db, query=None, semantic=False, user_filter=None):
    sessions = get_all_sessions_from_db(db, user_filter)
    with db_lock:
        total_msgs = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    newest = (utc_to_cet(sessions[0]["last_ts"] or sessions[0]["first_ts"]) or "?")[:10] if sessions else "?"

    # User filter buttons
    all_users = sorted(USERS.keys())
    user_btns = ""
    if len(all_users) > 1:
        active_all = "background:var(--accent);color:#fff;" if not user_filter else "background:var(--bg-input);color:var(--fg-dim);"
        user_btns = f'<div style="display:flex;gap:0.3rem;margin-bottom:1rem;"><a href="{BASE_PATH}/" style="padding:0.3rem 0.7rem;border-radius:4px;font-size:0.75rem;text-decoration:none;{active_all}">Alle</a>'
        for uid in all_users:
            active = "background:var(--accent);color:#fff;" if user_filter == uid else "background:var(--bg-input);color:var(--fg-dim);"
            user_btns += f'<a href="{BASE_PATH}/?user={uid}" style="padding:0.3rem 0.7rem;border-radius:4px;font-size:0.75rem;text-decoration:none;{active}">{uid.title()}</a>'
        user_btns += '</div>'

    search_html = ""
    if query and query.strip():
        if semantic:
            results = search_semantic(query.strip(), user_id=user_filter)
            items = []
            for r in results:
                snippet_html = md_to_html(r["text"][:300])
                relevance = max(0, 100 - int(r["distance"] * 50))
                badge = _user_badge(r.get("user_id", "anton"))
                anchor = f"#msg-{r['msg_idx']}" if r.get("msg_idx") is not None else ""
                items.append(f"""<a href="{BASE_PATH}/session/{r['session_id']}{anchor}" class="search-result" style="display:block;text-decoration:none;color:inherit;">
  <div class="result-session">{badge}{r['date']} — {'Anton' if r['role'] in ('user','human') else 'Eli'} — {html_mod.escape(r['title'])}</div>
  <div class="result-text">{snippet_html}</div>
  <div class="result-distance">Relevanz: {relevance}%</div>
</a>""")
            label = f'{len(results)} semantische Treffer für „{html_mod.escape(query.strip())}" <span class="sem-badge">KI-Suche</span>'
            search_html = f'<div class="search-results"><p style="color:#94a3b8;margin-bottom:0.8rem;">{label}</p>{"".join(items)}</div>'
        else:
            rows = search_fts(db, query.strip(), user_id=user_filter)
            items = []
            for r in rows:
                session_id, role, text, ts, title, first_ts, snippet, uid, msg_idx = r
                date_str = (utc_to_cet(first_ts) or "?")[:10]
                role_name = "Anton" if role in ("user", "human") else "Eli"
                badge = _user_badge(uid or "anton")
                anchor = f"#msg-{msg_idx}" if msg_idx is not None else ""
                items.append(f"""<a href="{BASE_PATH}/session/{session_id}{anchor}" class="search-result" style="display:block;text-decoration:none;color:inherit;">
  <div class="result-session">{badge}{date_str} — {role_name} — {html_mod.escape(title[:60])}</div>
  <div class="result-text">{snippet}</div>
</a>""")
            search_html = f'<div class="search-results"><p style="color:#94a3b8;margin-bottom:0.8rem;">{len(rows)} Treffer für „{html_mod.escape(query.strip())}"</p>{"".join(items)}</div>'

    rows = []
    for s in sessions:
        last_cet = utc_to_cet(s["last_ts"] or s["first_ts"])
        date_str = last_cet[:10] if last_cet else "?"
        time_str = last_cet[11:16] if last_cet and len(last_cet) > 15 else ""
        title_esc = html_mod.escape(s["title"][:100])
        badge = _user_badge(s.get("user_id", "anton"))
        # Summary + Tags
        summary = html_mod.escape(s.get("summary") or "")
        tags_json = s.get("tags")
        tags_html = ""
        if tags_json:
            try:
                tags = json.loads(tags_json)
                tags_html = " ".join(f'<a href="{BASE_PATH}/?q={html_mod.escape(t)}" class="tag">{html_mod.escape(t)}</a>' for t in tags)
            except (json.JSONDecodeError, TypeError):
                pass
        tags_block = f'<div class="session-tags">{tags_html}</div>' if tags_html else ""
        # Summary als Haupttext, Titel nur als Fallback wenn keine Summary
        if summary:
            main_text = summary
        else:
            main_text = title_esc
        rows.append(f"""<tr>
  <td class="user-col">{badge}</td>
  <td class="date">{date_str} <span class="time">{time_str}</span></td>
  <td><a href="{BASE_PATH}/session/{s['id']}">{main_text}</a>{tags_block}</td>
  <td class="num">{s['msg_count']}</td>
</tr>""")

    chroma_status = f' + {chroma_collection.count()} Embeddings' if chroma_collection else ''

    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session-Archiv — Eli & Anton</title>{STYLE}</head>
<body><div class="container">
<h1>Session-Archiv</h1>
<p class="subtitle">Eli & Team — alle Gespräche seit Januar 2026{chroma_status} · <a href="{BASE_PATH}/graph">Wissensgraph</a></p>
{user_btns}
<div class="stats">
  <div class="stat"><div class="stat-num">{len(sessions)}</div><div class="stat-label">Sessions</div></div>
  <div class="stat"><div class="stat-num">{total_msgs:,}</div><div class="stat-label">Nachrichten</div></div>
  <div class="stat"><div class="stat-num">{newest}</div><div class="stat-label">Neueste Session</div></div>
</div>
<div class="search-form">
  <input type="text" id="search-input" class="search-box"
    placeholder="Suche in allen Gesprächen..."
    value="{html_mod.escape(query or '')}" autofocus>
  <label class="search-toggle" title="Semantische Suche: findet auch verwandte Begriffe und Konzepte">
    <input type="checkbox" id="sem-toggle" {'checked' if semantic else ''}> Semantisch
  </label>
</div>
<div id="search-results">{search_html}</div>
<table id="session-table"><thead><tr>
  <th class="user-col"></th><th>Zuletzt</th><th>Thema</th><th style="text-align:right">Msgs</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table>
</div>
<script>
(function() {{
  const input = document.getElementById('search-input');
  const semToggle = document.getElementById('sem-toggle');
  const resultsDiv = document.getElementById('search-results');
  const table = document.getElementById('session-table');
  let debounceTimer = null;
  let abortCtrl = null;

  function doSearch() {{
    const q = input.value.trim();
    if (q.length < 2) {{
      resultsDiv.innerHTML = '';
      table.style.display = '';
      return;
    }}
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    const sem = semToggle.checked ? '&sem=1' : '';
    const userParam = new URLSearchParams(window.location.search).get('user');
    const userQ = userParam ? '&user=' + encodeURIComponent(userParam) : '';
    fetch('{BASE_PATH}/api/search?q=' + encodeURIComponent(q) + sem + userQ, {{ signal: abortCtrl.signal }})
      .then(r => r.json())
      .then(data => {{
        if (!data.results || data.results.length === 0) {{
          resultsDiv.innerHTML = '<p style="color:#94a3b8;">Keine Treffer.</p>';
          table.style.display = 'none';
          return;
        }}
        const isSem = data.mode === 'semantic';
        const semBadge = isSem ? ' <span class="sem-badge">KI-Suche</span>' : '';
        let html = '<div class="search-results"><p style="color:#94a3b8;margin-bottom:0.8rem;">' +
          data.results.length + (isSem ? ' semantische' : '') + ' Treffer für \u201e' +
          escHtml(q) + '\u201c' + semBadge + '</p>';
        const userColors = {{anton: 'var(--accent)', timo: '#e5a33d'}};
        function userBadge(uid) {{
          const c = userColors[uid] || 'var(--fg-dim)';
          const i = (uid || '?')[0].toUpperCase();
          return '<span style="display:inline-block;width:1.3em;height:1.3em;line-height:1.3em;text-align:center;border-radius:50%;background:' + c + ';color:#fff;font-size:0.6rem;font-weight:600;margin-right:0.4rem;vertical-align:middle;">' + i + '</span>';
        }}
        data.results.forEach(r => {{
          const role = (r.role === 'user' || r.role === 'human') ? 'Anton' : 'Eli';
          const date = r.date || '?';
          const title = escHtml((r.title || '').substring(0, 60));
          const text = r.snippet || r.html || escHtml((r.text || '').substring(0, 300));
          const dist = (typeof r.distance === 'number') ?
            '<div class="result-distance">Relevanz: ' + Math.max(0, 100 - Math.round(r.distance * 50)) + '%</div>' : '';
          const ub = userBadge(r.user_id || 'anton');
          const anchor = (r.msg_idx != null) ? '#msg-' + r.msg_idx : '';
          html += '<a href="{BASE_PATH}/session/' + r.session_id + anchor + '" class="search-result" style="display:block;text-decoration:none;color:inherit;">' +
            '<div class="result-session">' + ub + date + ' \u2014 ' + role + ' \u2014 ' + title + '</div>' +
            '<div class="result-text">' + text + '</div>' + dist + '</a>';
        }});
        html += '</div>';
        resultsDiv.innerHTML = html;
        table.style.display = 'none';
      }})
      .catch(e => {{ if (e.name !== 'AbortError') console.error(e); }});
  }}

  function escHtml(s) {{
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }}

  function onInput() {{
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 2) {{
      resultsDiv.innerHTML = '';
      table.style.display = '';
      return;
    }}
    debounceTimer = setTimeout(doSearch, 250);
  }}

  input.addEventListener('input', onInput);
  semToggle.addEventListener('change', () => {{
    if (input.value.trim().length >= 2) doSearch();
  }});

  // If page loaded with query, results are already server-rendered
  if (input.value.trim().length >= 2) {{
    table.style.display = 'none';
  }}
}})();
</script>
</body></html>"""


def render_session_page(db, session_id):
    with db_lock:
        session = db.execute(
            "SELECT id, title, first_ts, msg_count, summary, tags FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not session:
        return None

    sid, title, first_ts, msg_count, summary, tags_json = session
    date_str = utc_to_cet(first_ts) or "?"
    title_esc = html_mod.escape(title[:120])
    # Tags + Summary for session header
    session_meta_extra = ""
    if summary:
        session_meta_extra += f'<div class="session-summary" style="margin-top:0.5rem; font-style:italic; opacity:0.8;">{html_mod.escape(summary)}</div>'
    if tags_json:
        try:
            tags = json.loads(tags_json)
            tags_html = " ".join(f'<a href="{BASE_PATH}/?q={html_mod.escape(t)}" class="tag">{html_mod.escape(t)}</a>' for t in tags)
            session_meta_extra += f'<div class="session-tags" style="margin-top:0.3rem; margin-bottom:1.5rem;">{tags_html}</div>'
        except (json.JSONDecodeError, TypeError):
            pass

    messages = get_session_messages(db, session_id)
    msgs_html = []
    for i, msg in enumerate(messages):
        role = msg["role"]
        if role in ("user", "human"):
            css, name = "msg-anton", "Anton"
        elif role == "assistant":
            css, name = "msg-eli", "Eli"
        else:
            css, name = "msg-system", "System"
        ts_cet = utc_to_cet(msg["timestamp"])
        ts = ts_cet[11:16] if ts_cet and len(ts_cet) > 15 else ""
        body = md_to_html(msg["text"])
        msgs_html.append(f"""<div id="msg-{i}" class="msg {css}">
  <div class="msg-body">{body}</div>
  <span class="msg-time">{ts}</span>
</div>""")

    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_esc} — Session-Archiv</title>{STYLE}</head>
<body><div class="container">
<div class="back"><a href="{BASE_PATH}/">&larr; Alle Sessions</a></div>
<h1>{title_esc}</h1>
<div class="session-meta">{date_str} — {msg_count} Nachrichten — <code>{sid[:8]}</code></div>
{session_meta_extra}
{"".join(msgs_html)}
<div class="back" style="margin-top:2rem;"><a href="{BASE_PATH}/">&larr; Alle Sessions</a></div>
</div>
<style>.msg.highlight {{ outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 8px; }}</style>
<script>
if (location.hash) {{
  const el = document.querySelector(location.hash);
  if (el) {{
    el.classList.add('highlight');
    setTimeout(() => el.scrollIntoView({{ behavior: 'smooth', block: 'center' }}), 100);
  }}
}}
</script>
</body></html>"""


# ── JSON API for Eli ─────────────────────────────────────

def api_search(db, query: str, semantic=False, limit=50, user_id=None, full=False):
    """JSON API endpoint for programmatic access."""
    text_limit = 0 if full else 300  # 0 = no limit
    if semantic:
        results = search_semantic(query, limit, user_id)
        for r in results:
            t = r["text"] if full else r["text"][:300]
            r["html"] = md_to_html(t)
        return json.dumps({"mode": "semantic", "query": query, "results": results}, ensure_ascii=False)
    else:
        rows = search_fts(db, query, limit, user_id)
        results = []
        for r in rows:
            session_id, role, text, ts, title, first_ts, snippet, uid, msg_idx = r
            t = text if full else text[:300]
            results.append({
                "session_id": session_id,
                "user_id": uid or "anton",
                "role": role,
                "text": t,
                "snippet": snippet,
                "html": md_to_html(t),
                "date": (utc_to_cet(first_ts) or "?")[:10],
                "title": title,
                "msg_idx": msg_idx,
            })
        return json.dumps({"mode": "fulltext", "query": query, "results": results}, ensure_ascii=False)


# ── HTTP Server ──────────────────────────────────────────

class ArchiveHandler(BaseHTTPRequestHandler):
    db = None

    def check_auth(self, params):
        """Check API key if one is configured. Returns True if OK."""
        if not API_KEY:
            return True  # no key configured = open access (local use)
        # Check query parameter
        key = params.get("key", [None])[0]
        if key == API_KEY:
            self._set_auth_cookie = True
            return True
        # Check Authorization header
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {API_KEY}":
            return True
        # Check cookie
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("archive_key=") and part[12:] == API_KEY:
                return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if not self.check_auth(params):
            self.respond(401, '{"error": "unauthorized"}', content_type="application/json")
            return

        if path == "/" or path == "":
            query = params.get("q", [None])[0]
            semantic = params.get("sem", [""])[0] == "1"
            user_filter = params.get("user", [None])[0]
            body = render_index(self.db, query, semantic, user_filter)
            self.respond(200, body)

        elif path == "/api/search":
            query = params.get("q", [""])[0]
            semantic = params.get("sem", [""])[0] == "1"
            full = params.get("full", [""])[0] == "1"
            limit = int(params.get("limit", ["50"])[0])
            user_filter = params.get("user", [None])[0]
            body = api_search(self.db, query, semantic, limit, user_filter, full)
            self.respond(200, body, content_type="application/json; charset=utf-8")

        elif path == "/graph":
            body = render_graph_page()
            self.respond(200, body)

        elif path == "/api/graph":
            data = get_graph_data()
            self.respond(200, json.dumps(data, ensure_ascii=False), content_type="application/json; charset=utf-8")

        elif path == "/api/graph/memories":
            neo_id = int(params.get("neo_id", ["0"])[0])
            data = get_node_memories(neo_id)
            self.respond(200, json.dumps(data, ensure_ascii=False), content_type="application/json; charset=utf-8")

        elif path == "/api/graph/query":
            qt = params.get("type", [""])[0]
            # Pass all other params to graph_query
            qp = {k: v[0] for k, v in params.items() if k != "type" and k != "key"}
            data = graph_query(qt, qp)
            self.respond(200, json.dumps(data, ensure_ascii=False), content_type="application/json; charset=utf-8")

        elif path.startswith("/session/"):
            session_id = unquote(path[9:])
            body = render_session_page(self.db, session_id)
            if body:
                self.respond(200, body)
            else:
                self.respond(404, "<h1>Session nicht gefunden</h1>")

        else:
            self.respond(404, "<h1>404</h1>")

    def respond(self, code, body, content_type="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        # Set auth cookie when key was provided via query param
        if getattr(self, "_set_auth_cookie", False):
            self.send_header("Set-Cookie", f"archive_key={API_KEY}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
            self._set_auth_cookie = False
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Session Archive Server")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--users", nargs="*", help="user:path pairs, e.g. anton:/path/to/jsonl timo:/other/path")
    parser.add_argument("--api-key", help="API key for remote access (or set ARCHIVE_API_KEY env)")
    parser.add_argument("--base-path", help="URL prefix for reverse proxy (e.g. /archive)")
    parser.add_argument("--rebuild-chroma", action="store_true", help="Delete and rebuild Chroma embeddings from scratch")
    args = parser.parse_args()

    # Configure API key
    global API_KEY
    if args.api_key:
        API_KEY = args.api_key

    # Configure base path
    global BASE_PATH
    if args.base_path:
        BASE_PATH = args.base_path.rstrip("/")

    # Configure user directories
    if args.users:
        for spec in args.users:
            if ":" in spec:
                uid, path = spec.split(":", 1)
                USERS[uid] = path
    if not USERS:
        # Default: single user "anton" from default directory
        USERS[DEFAULT_USER] = _default_dir

    print("Session-Archiv wird initialisiert...")
    print(f"  Nutzer: {', '.join(f'{uid} → {path}' for uid, path in USERS.items())}")

    # Init SQLite
    db = init_db()
    print(f"  SQLite: {DB_PATH}")

    # Initial index (SQLite only, fast)
    print("  Indexiere Sessions (SQLite)...")
    index_sessions(db)

    total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_msgs = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print(f"  Bereit: {total} Sessions, {total_msgs} Nachrichten")
    print(f"  Chroma-Embeddings werden im Hintergrund geladen...")

    # Background: init Chroma + re-index with embeddings, then periodic re-index
    def reindex_loop():
        init_chroma(force_rebuild=args.rebuild_chroma)
        if chroma_collection:
            index_sessions(db)  # re-index to fill Chroma
            print(f"  Chroma bereit: {chroma_collection.count()} Dokumente")
        while True:
            time_mod.sleep(60)
            try:
                index_sessions(db)
            except Exception as e:
                print(f"  Re-Index Fehler: {e}")

    t = threading.Thread(target=reindex_loop, daemon=True)
    t.start()

    # Background: generate summaries + tags for sessions that don't have them
    def summary_loop():
        time_mod.sleep(10)  # wait for initial index
        while True:
            try:
                with db_lock:
                    pending = db.execute(
                        "SELECT id FROM sessions WHERE summary IS NULL AND msg_count >= 4 ORDER BY first_ts DESC"
                    ).fetchall()
                if pending:
                    print(f"  Summaries: {len(pending)} Sessions ohne Summary")
                for row in pending:
                    sid = row[0]
                    msgs = get_session_messages(db, sid)
                    if len(msgs) < 4:
                        continue
                    result = generate_summary_and_tags(msgs)
                    if result:
                        tags_json = json.dumps(result["tags"], ensure_ascii=False)
                        graph_json = json.dumps({
                            "projects": result.get("projects", []),
                            "mentions": result.get("mentions", []),
                        }, ensure_ascii=False)
                        with db_lock:
                            db.execute(
                                "UPDATE sessions SET summary = ?, tags = ?, graph_data = ? WHERE id = ?",
                                (result["summary"], tags_json, graph_json, sid),
                            )
                            db.commit()
                        print(f"  Summary: {sid[:8]} — {result['tags']} | projects={result.get('projects', [])}")
                    time_mod.sleep(1)  # rate limit
            except Exception as e:
                print(f"  Summary-Fehler: {e}")
            time_mod.sleep(300)  # check every 5 min for new sessions

    if ANTHROPIC_API_KEY:
        t2 = threading.Thread(target=summary_loop, daemon=True)
        t2.start()
        print("  Summaries werden im Hintergrund generiert...")
    else:
        print("  Summaries deaktiviert (kein ANTHROPIC_API_KEY)")

    # Background: Neo4j graph sync
    def neo4j_sync_loop():
        time_mod.sleep(30)  # wait for Chroma + summaries
        init_neo4j()
        if not neo4j_driver:
            return
        sync_to_neo4j(db)  # initial full sync
        while True:
            time_mod.sleep(600)  # re-sync every 10 min
            try:
                sync_to_neo4j(db)
            except Exception as e:
                print(f"  Neo4j sync Fehler: {e}")

    if NEO4J_URI:
        t3 = threading.Thread(target=neo4j_sync_loop, daemon=True)
        t3.start()
        print("  Neo4j-Graph wird im Hintergrund aufgebaut...")
    else:
        print("  Neo4j deaktiviert (kein NEO4J_URI)")

    # Start server
    ArchiveHandler.db = db
    server = HTTPServer(("0.0.0.0", args.port), ArchiveHandler)
    print(f"\nSession-Archiv: http://localhost:{args.port}")
    print(f"  JSON API:     http://localhost:{args.port}/api/search?q=...&sem=1")
    print(f"  Wissensgraph: http://localhost:{args.port}/graph")
    print(f"  Strg+C zum Beenden")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
        db.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Migrate Questions → Offene Punkte.

1. Delete all 136 old Question nodes + RAISED edges
2. Create 13 curated open points with better schema (name, status, project, date)

Run: docker compose run --rm eli-archive python /app/migrate_questions.py
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, '/app')

from neo4j import GraphDatabase

neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://eli-neo4j:7687')
neo4j_user = os.environ.get('NEO4J_USER', 'neo4j')
neo4j_pass = os.environ.get('NEO4J_PASSWORD', '')
driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

# === The 13 real open points ===
OPEN_POINTS = [
    {
        "name": "Balance zwischen Eigeninitiative und Vertrauen in andere",
        "project": None,
        "status": "open",
    },
    {
        "name": "Verifications vs. Attestations — begriffliche Klärung",
        "project": "web-of-trust",
        "status": "open",
    },
    {
        "name": "Automerge-Transition: Evolu ersetzen für Cross-User Spaces",
        "project": "web-of-trust",
        "status": "open",
    },
    {
        "name": "WoT-Philosophie verständlich kommunizieren",
        "project": "web-of-trust",
        "status": "open",
    },
    {
        "name": "Kooperation und Rollen-Zuweisung in dezentralen Netzwerken",
        "project": "real-life-network",
        "status": "open",
    },
    {
        "name": "Persönliche Gespräche in Eli's Erinnerungen — Ethik und Grenzen",
        "project": "eli",
        "status": "open",
    },
    {
        "name": "Erinnerungssystem für Tillmann kapseln (lokales Deployment)",
        "project": "eli",
        "status": "open",
    },
    {
        "name": "Eli Multi-User Identitätsverifikation",
        "project": "eli",
        "status": "open",
    },
    {
        "name": "Gruppenprozesse automatisch dokumentieren und als Geschichte erzählen",
        "project": "real-life-stack",
        "status": "open",
    },
    {
        "name": "Rechtsform für das Netzwerk (Schweizer Stiftung?)",
        "project": "real-life-network",
        "status": "open",
    },
    {
        "name": "Funding-Strategie: Höhe, Quelle, Zeitplan",
        "project": "real-life-network",
        "status": "open",
    },
    {
        "name": "GPU-Hardware für Whisper Echtzeit-Transkription",
        "project": None,
        "status": "open",
    },
    {
        "name": "Recovery-Codes A7-Format Design",
        "project": "web-of-trust",
        "status": "open",
    },
]

print("=== Phase 1: Alte Questions löschen ===\n")

with driver.session() as s:
    result = s.run("MATCH (q:Question) RETURN count(q) as c")
    old_count = result.single()["c"]

    result = s.run("MATCH ()-[r:RAISED]->() RETURN count(r) as c")
    edge_count = result.single()["c"]

    print(f"  Question-Knoten: {old_count}")
    print(f"  RAISED-Kanten: {edge_count}")

    s.run("MATCH (q:Question) DETACH DELETE q")
    print(f"  → Alle {old_count} Questions + {edge_count} RAISED gelöscht")

print("\n=== Phase 2: Offene Punkte anlegen ===\n")

with driver.session() as s:
    for point in OPEN_POINTS:
        params = {
            "name": point["name"],
            "status": point["status"],
        }

        # Create Question node with status + optional project
        if point["project"]:
            params["project"] = point["project"]
            s.run("""
                MERGE (q:Question {name: $name})
                SET q.status = $status, q.project = $project
                WITH q
                MATCH (p:Project {name: $project})
                MERGE (q)-[:ABOUT]->(p)
            """, **params)
            print(f"  ✓ {point['name']} → {point['project']}")
        else:
            s.run("""
                MERGE (q:Question {name: $name})
                SET q.status = $status
            """, **params)
            print(f"  ✓ {point['name']}")

    # Final count
    result = s.run("MATCH (q:Question) RETURN count(q) as c")
    new_count = result.single()["c"]
    result = s.run("MATCH (q:Question)-[r:ABOUT]->(p:Project) RETURN count(r) as c")
    about_count = result.single()["c"]
    print(f"\n  Offene Punkte: {new_count}, ABOUT-Kanten zu Projekten: {about_count}")

print("\n=== Phase 3: SQLite — questions aus graph_data bereinigen ===\n")

db_path = os.path.join(os.environ.get('DB_DIR', '/app/archive'), 'archive.db')
db = sqlite3.connect(db_path)

cursor = db.execute("SELECT id, graph_data FROM sessions WHERE graph_data IS NOT NULL")
rows = cursor.fetchall()
updated = 0

for session_id, graph_data_json in rows:
    try:
        gd = json.loads(graph_data_json)
    except (json.JSONDecodeError, TypeError):
        continue

    if "questions" in gd:
        del gd["questions"]
        db.execute("UPDATE sessions SET graph_data = ? WHERE id = ?",
                   (json.dumps(gd, ensure_ascii=False), session_id))
        updated += 1

db.commit()
db.close()
print(f"  {updated} Sessions: 'questions' aus graph_data entfernt")

driver.close()
print("\nFertig!")

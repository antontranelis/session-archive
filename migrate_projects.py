#!/usr/bin/env python3
"""
Migrate from Concepts to Projects.

1. Neo4j: Delete all Concept nodes + DISCUSSES edges
2. Neo4j: Convert project-named Tags → Project nodes + BELONGS_TO edges
3. Neo4j: Remove project-named Tags from Tag nodes
4. SQLite: Update graph_data (concepts → projects), re-normalize tags

Run: docker compose run --rm eli-archive python /app/migrate_projects.py
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, '/app')
from serve import PROJECTS, TAG_MIGRATION, ALLOWED_TAGS, normalize_tags

from neo4j import GraphDatabase

neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://eli-neo4j:7687')
neo4j_user = os.environ.get('NEO4J_USER', 'neo4j')
neo4j_pass = os.environ.get('NEO4J_PASSWORD', '')
driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

print("=== Phase 1: Neo4j — Concepts löschen ===\n")

with driver.session() as s:
    result = s.run("MATCH (c:Concept) RETURN count(c) as c")
    count = result.single()["c"]
    print(f"  Concept-Knoten: {count}")

    result = s.run("MATCH ()-[r:DISCUSSES]->() RETURN count(r) as c")
    edge_count = result.single()["c"]
    print(f"  DISCUSSES-Kanten: {edge_count}")

    s.run("MATCH (c:Concept) DETACH DELETE c")
    print(f"  → Alle {count} Concepts + {edge_count} DISCUSSES gelöscht")

print("\n=== Phase 2: Neo4j — Projekt-Tags → Project-Knoten ===\n")

with driver.session() as s:
    # Create Project constraint
    try:
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:Project) REQUIRE p.name IS UNIQUE")
    except Exception:
        pass

    for project_name in sorted(PROJECTS):
        # Check if this project exists as a Tag
        result = s.run("MATCH (t:Tag {name: $name}) RETURN t", name=project_name)
        tag_exists = result.single() is not None

        # Create Project node
        s.run("MERGE (p:Project {name: $name})", name=project_name)

        if tag_exists:
            # Move TAGGED edges to BELONGS_TO edges
            result = s.run("""
                MATCH (session:Session)-[:TAGGED]->(t:Tag {name: $name})
                MATCH (p:Project {name: $name})
                WHERE NOT (session)-[:BELONGS_TO]->(p)
                MERGE (session)-[:BELONGS_TO]->(p)
                RETURN count(*) as moved
            """, name=project_name)
            moved = result.single()["moved"]
            # Delete the Tag node
            s.run("MATCH (t:Tag {name: $name}) DETACH DELETE t", name=project_name)
            print(f"  {project_name}: Tag → Project ({moved} Sessions)")
        else:
            print(f"  {project_name}: Project erstellt (noch keine Sessions)")

    # Final counts
    result = s.run("MATCH (p:Project) RETURN count(p) as c")
    proj_count = result.single()["c"]
    result = s.run("MATCH ()-[r:BELONGS_TO]->() RETURN count(r) as c")
    bt_count = result.single()["c"]
    result = s.run("MATCH (t:Tag) RETURN count(t) as c")
    tag_count = result.single()["c"]
    print(f"\n  Projekte: {proj_count}, BELONGS_TO-Kanten: {bt_count}, Tags verbleibend: {tag_count}")

    # Delete orphan tags (no edges)
    result = s.run("""
        MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-()
        RETURN t.name as name
    """)
    orphans = [r["name"] for r in result]
    if orphans:
        s.run("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DETACH DELETE t")
        print(f"  Verwaiste Tags gelöscht: {orphans}")

driver.close()

print("\n=== Phase 3: SQLite — graph_data + tags updaten ===\n")

db_path = os.path.join(os.environ.get('DB_DIR', '/app/archive'), 'archive.db')
db = sqlite3.connect(db_path)

# Update graph_data: concepts → projects (map concept names to project names where possible)
cursor = db.execute("SELECT id, tags, graph_data FROM sessions")
rows = cursor.fetchall()
updated = 0

for session_id, tags_json, graph_data_json in rows:
    changed = False

    # Re-normalize tags: split out project names
    if tags_json:
        try:
            old_tags = json.loads(tags_json)
            new_tags, new_projects = normalize_tags(old_tags)
            if sorted(old_tags) != new_tags:
                db.execute("UPDATE sessions SET tags = ? WHERE id = ?",
                           (json.dumps(new_tags, ensure_ascii=False), session_id))
                changed = True
        except (json.JSONDecodeError, TypeError):
            new_projects = []
    else:
        new_projects = []

    # Update graph_data: remove concepts, add projects
    if graph_data_json:
        try:
            gd = json.loads(graph_data_json)
            if "concepts" in gd:
                del gd["concepts"]
            # Merge projects from tags + any existing
            existing_projects = set(gd.get("projects", []))
            existing_projects.update(new_projects)
            gd["projects"] = sorted(existing_projects)
            db.execute("UPDATE sessions SET graph_data = ? WHERE id = ?",
                       (json.dumps(gd, ensure_ascii=False), session_id))
            changed = True
        except (json.JSONDecodeError, TypeError):
            pass

    if changed:
        updated += 1

db.commit()
db.close()
print(f"  {updated} Sessions aktualisiert")
print("\nFertig!")

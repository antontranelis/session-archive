#!/usr/bin/env python3
"""
Migrate tags in Neo4j and SQLite to the curated tag set.

1. Neo4j: Merge old Tag nodes → new Tag nodes, move TAGGED edges, delete orphans
2. SQLite: Update tags JSON in sessions table

Run inside docker: docker compose run --rm eli-archive python /app/migrate_tags.py
"""
import json
import os
import sqlite3
from neo4j import GraphDatabase

# Import the mapping from serve.py
import sys
sys.path.insert(0, '/app')
from serve import TAG_MIGRATION, ALLOWED_TAGS, normalize_tags

# --- Neo4j ---
neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://eli-neo4j:7687')
neo4j_user = os.environ.get('NEO4J_USER', 'neo4j')
neo4j_pass = os.environ.get('NEO4J_PASSWORD', '')
driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

print("=== Neo4j Tag-Migration ===\n")

with driver.session() as s:
    # Step 1: Get all current tags
    result = s.run("MATCH (t:Tag) RETURN t.name as name, id(t) as tid")
    all_tags = {r["name"]: r["tid"] for r in result}
    print(f"Aktuelle Tags: {len(all_tags)}")

    # Step 2: For each tag that needs migration
    merged = 0
    deleted = 0
    for old_tag, new_tag in TAG_MIGRATION.items():
        if old_tag not in all_tags:
            continue

        if new_tag is None:
            # Delete: move no edges, just disconnect and delete
            s.run("MATCH (t:Tag {name: $name}) DETACH DELETE t", name=old_tag)
            deleted += 1
            print(f"  GELÖSCHT: {old_tag}")
        else:
            # Merge: ensure target exists, move edges, delete old
            s.run("MERGE (t:Tag {name: $name})", name=new_tag)
            # Move TAGGED edges from old → new (avoid duplicates)
            result = s.run("""
                MATCH (session)-[:TAGGED]->(old:Tag {name: $old_name})
                MATCH (new:Tag {name: $new_name})
                WHERE NOT (session)-[:TAGGED]->(new)
                MERGE (session)-[:TAGGED]->(new)
                RETURN count(*) as moved
            """, old_name=old_tag, new_name=new_tag)
            moved = result.single()["moved"]
            # Delete old tag node
            s.run("MATCH (t:Tag {name: $name}) DETACH DELETE t", name=old_tag)
            merged += 1
            if moved > 0:
                print(f"  {old_tag} → {new_tag} ({moved} Verbindungen verschoben)")
            else:
                print(f"  {old_tag} → {new_tag} (keine neuen Verbindungen nötig)")

    # Step 3: Delete orphan tags (no TAGGED edges)
    result = s.run("""
        MATCH (t:Tag)
        WHERE NOT (t)<-[:TAGGED]-()
        RETURN t.name as name
    """)
    orphans = [r["name"] for r in result]
    if orphans:
        s.run("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DETACH DELETE t")
        print(f"\n  Verwaiste Tags gelöscht: {orphans}")

    # Step 4: Final count
    result = s.run("MATCH (t:Tag) RETURN count(t) as c")
    final_count = result.single()["c"]
    print(f"\nNeo4j: {merged} gemergt, {deleted} gelöscht, {len(orphans)} verwaiste entfernt")
    print(f"Tags vorher: {len(all_tags)} → nachher: {final_count}")

    # Show remaining tags
    result = s.run("""
        MATCH (t:Tag)<-[:TAGGED]-(s)
        RETURN t.name as name, count(s) as sessions
        ORDER BY sessions DESC
    """)
    print("\nVerbleibende Tags:")
    for r in result:
        marker = " ✓" if r["name"] in ALLOWED_TAGS else " ⚠ (nicht im Set)"
        print(f"  {r['sessions']:3d} {r['name']}{marker}")

driver.close()

# --- SQLite ---
print("\n\n=== SQLite Tag-Migration ===\n")

db_path = os.environ.get('DB_DIR', '/app/archive')
db_file = os.path.join(db_path, 'archive.db')

if not os.path.exists(db_file):
    print(f"DB nicht gefunden: {db_file}")
    sys.exit(1)

db = sqlite3.connect(db_file)
cursor = db.execute("SELECT id, tags FROM sessions WHERE tags IS NOT NULL AND tags != ''")
rows = cursor.fetchall()
print(f"Sessions mit Tags: {len(rows)}")

updated = 0
for session_id, tags_json in rows:
    try:
        old_tags = json.loads(tags_json)
    except json.JSONDecodeError:
        continue

    new_tags = normalize_tags(old_tags)

    if sorted(old_tags) != new_tags:
        db.execute("UPDATE sessions SET tags = ? WHERE id = ?",
                   (json.dumps(new_tags, ensure_ascii=False), session_id))
        updated += 1

db.commit()
db.close()
print(f"SQLite: {updated} Sessions aktualisiert")
print("\nFertig!")

#!/usr/bin/env python3
"""
Fix Personen-Knoten im Graph:
1. kontext → beschreibung (damit die UI den Text anzeigt)
2. Rainer löschen (kein echter Kontakt von Anton)
3. KENNT-Kanten deduplizieren: wenn A ARBEITET_MIT B, dann keine separate KENNT-Kante nötig
   (ARBEITET_MIT impliziert KENNT — Doppel-Kanten entfernen)

Usage: python3 fix_personen_graph.py
"""
import os
import sys
from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")


def main():
    if not NEO4J_PASSWORD:
        print("⛔ NEO4J_PASSWORD nicht gesetzt!")
        sys.exit(1)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Verbunden.")

    with driver.session() as s:
        # 1. kontext → beschreibung für alle Personen
        result = s.run("""
            MATCH (p:Person)
            WHERE p.kontext IS NOT NULL AND p.kontext <> ''
            SET p.beschreibung = p.kontext
            REMOVE p.kontext
            RETURN count(p) as n
        """)
        n = result.single()["n"]
        print(f"1. kontext→beschreibung: {n} Personen aktualisiert")

        # 2. Auch für Personen ohne kontext: sicherstellen dass beschreibung existiert
        result = s.run("""
            MATCH (p:Person)
            WHERE p.beschreibung IS NULL OR p.beschreibung = ''
            RETURN p.name as name, p.rolle as rolle
            ORDER BY p.name
        """)
        rows = list(result)
        print(f"   Personen ohne beschreibung: {len(rows)}")
        for r in rows:
            print(f"   - {r['name']}: {r['rolle'] or '(keine Rolle)'}")

        # 3. Rainer löschen
        result = s.run("""
            MATCH (p:Person {name: 'rainer'})
            DETACH DELETE p
            RETURN count(p) as n
        """)
        # DETACH DELETE gibt count vor dem Löschen zurück — prüfe danach
        check = s.run("MATCH (p:Person {name: 'rainer'}) RETURN count(p) as n").single()["n"]
        print(f"2. Rainer gelöscht (verbleibend: {check})")

        # 4. KENNT-Kanten entfernen wo ARBEITET_MIT bereits existiert
        # (ARBEITET_MIT impliziert Bekanntschaft — Duplikat-Kanten bereinigen)
        result = s.run("""
            MATCH (a:Person)-[k:KENNT]->(b:Person)
            WHERE (a)-[:ARBEITET_MIT]->(b)
            DELETE k
            RETURN count(k) as n
        """)
        n = result.single()["n"]
        print(f"3. Duplikate KENNT/ARBEITET_MIT: {n} überflüssige KENNT-Kanten entfernt")

        # Statistiken danach
        total_persons = s.run("MATCH (p:Person) RETURN count(p) as n").single()["n"]
        total_kennt = s.run("MATCH ()-[r:KENNT]->() RETURN count(r) as n").single()["n"]
        total_arbeitet = s.run("MATCH ()-[r:ARBEITET_MIT]->() RETURN count(r) as n").single()["n"]
        total_edges = s.run("MATCH ()-[r]->() RETURN count(r) as n").single()["n"]
        print(f"\n=== Ergebnis ===")
        print(f"Personen: {total_persons}")
        print(f"KENNT-Kanten: {total_kennt}")
        print(f"ARBEITET_MIT-Kanten: {total_arbeitet}")
        print(f"Kanten gesamt: {total_edges}")

    driver.close()
    print("Fertig.")


if __name__ == "__main__":
    main()

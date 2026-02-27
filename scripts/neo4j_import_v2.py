#!/usr/bin/env python3
"""
Neo4j Import v2 — Graph komplett neu aufbauen aus gemergten Daten.

Input:  merged/{typ}.json
Output: Neo4j-Graph

Usage:
  python3 neo4j_import_v2.py
  python3 neo4j_import_v2.py --backup-only  # Nur Backup erstellen
  python3 neo4j_import_v2.py --dry-run      # Nur zählen, kein Import

Voraussetzung: neo4j Python-Paket installiert.
  pip install neo4j
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(SCRIPT_DIR.parent)))
MERGED_DIR = DATA_DIR / "merged"
BACKUP_DIR = DATA_DIR / "backups"


def connect():
    """Verbindet mit Neo4j."""
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    return driver


def backup_graph(driver):
    """Erstellt ein JSON-Backup des gesamten Graphen."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"neo4j_backup_v2_{timestamp}.json"

    with driver.session() as session:
        # Knoten
        nodes_result = session.run("""
            MATCH (n)
            RETURN labels(n) as labels, properties(n) as props, elementId(n) as id
        """)
        nodes = [{"labels": r["labels"], "props": dict(r["props"]), "id": r["id"]} for r in nodes_result]

        # Kanten
        edges_result = session.run("""
            MATCH (a)-[r]->(b)
            RETURN type(r) as type, properties(r) as props,
                   elementId(a) as from_id, elementId(b) as to_id,
                   labels(a) as from_labels, properties(a) as from_props,
                   labels(b) as to_labels, properties(b) as to_props
        """)
        edges = [{
            "type": r["type"],
            "props": dict(r["props"]) if r["props"] else {},
            "from": {"labels": r["from_labels"], "name": r["from_props"].get("name", "?")},
            "to": {"labels": r["to_labels"], "name": r["to_props"].get("name", "?")},
        } for r in edges_result]

    backup = {"nodes": nodes, "edges": edges, "timestamp": timestamp}
    with open(backup_path, "w") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)

    print(f"Backup: {backup_path} ({len(nodes)} Knoten, {len(edges)} Kanten)")
    return backup_path


def clear_graph(driver):
    """Löscht alle Knoten und Kanten."""
    with driver.session() as session:
        result = session.run("MATCH (n) RETURN count(n) as c").single()
        count = result["c"]
        print(f"Lösche {count} Knoten...")
        session.run("MATCH (n) DETACH DELETE n")


def create_constraints(driver):
    """Erstellt Unique-Constraints und Indizes."""
    constraints = [
        ("Person", "name"),
        ("Organisation", "name"),
        ("Projekt", "name"),
        ("Thema", "name"),
        ("Erkenntnis", "name"),
        ("Entscheidung", "name"),
        ("Meilenstein", "name"),
        ("Herausforderung", "name"),
        ("Spannung", "name"),
        ("Frage", "text"),
    ]
    with driver.session() as session:
        for label, prop in constraints:
            try:
                session.run(f"""
                    CREATE CONSTRAINT IF NOT EXISTS
                    FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE
                """)
            except Exception:
                pass  # Constraint existiert bereits


def import_personen(driver, data):
    """Importiert Personen-Knoten."""
    with driver.session() as session:
        for p in data:
            props = {
                "name": p["name"],
                "rolle": p.get("rolle"),
                "kontext": p.get("kontext"),
                "angebote": p.get("angebote", []),
                "bedürfnisse": p.get("bedürfnisse", []),
                "session_ids": p.get("session_ids", []),
                "quelle": p.get("_quelle", "session"),
            }
            # None-Werte entfernen
            props = {k: v for k, v in props.items() if v is not None}
            session.run("MERGE (p:Person {name: $name}) SET p += $props",
                        name=p["name"], props=props)


def import_organisationen(driver, data):
    """Importiert Organisations-Knoten."""
    with driver.session() as session:
        for o in data:
            props = {
                "name": o["name"],
                "beschreibung": o.get("beschreibung"),
                "session_ids": o.get("session_ids", []),
                "quelle": o.get("_quelle", "session"),
            }
            props = {k: v for k, v in props.items() if v is not None}
            session.run("MERGE (o:Organisation {name: $name}) SET o += $props",
                        name=o["name"], props=props)


def import_projekte(driver, data):
    """Importiert Projekt-Knoten."""
    with driver.session() as session:
        for p in data:
            props = {
                "name": p["name"],
                "beschreibung": p.get("beschreibung"),
                "status": p.get("status"),
                "session_ids": p.get("session_ids", []),
            }
            props = {k: v for k, v in props.items() if v is not None}
            session.run("MERGE (p:Projekt {name: $name}) SET p += $props",
                        name=p["name"], props=props)


def import_themen(driver, data):
    """Importiert Themen-Knoten (einfache Strings)."""
    with driver.session() as session:
        for t in data:
            if isinstance(t, str):
                session.run("MERGE (t:Thema {name: $name})", name=t)


def import_content_nodes(driver, data, label, name_field="name"):
    """Importiert inhaltliche Knoten (Erkenntnis, Entscheidung, etc.)."""
    with driver.session() as session:
        for item in data:
            name = item.get(name_field, item.get("text", ""))
            if not name:
                continue
            props = dict(item)
            # Listen die als Kanten modelliert werden entfernen
            for k in ["personen", "themen", "projekt", "zwischen_personen",
                       "zwischen_konzepte", "_quelle", "session_ids"]:
                props.pop(k, None)
            props["session_ids"] = item.get("session_ids", [])
            props["msg_refs"] = item.get("msg_refs", [])
            props["quelle"] = item.get("_quelle", "session")

            session.run(
                f"MERGE (n:{label} {{{name_field}: $name}}) SET n += $props",
                name=name, props=props,
            )


def create_edges(driver, merged_data):
    """Erstellt alle Kanten basierend auf den gemergten Daten."""
    with driver.session() as session:
        # Person → Person (KENNT, ARBEITET_MIT)
        for p in merged_data.get("personen", []):
            name = p["name"]
            for other in p.get("kennt", []):
                session.run("""
                    MATCH (a:Person {name: $from}), (b:Person {name: $to})
                    MERGE (a)-[:KENNT]->(b)
                """, **{"from": name, "to": other})

            for other in p.get("arbeitet_mit", []):
                session.run("""
                    MATCH (a:Person {name: $from}), (b:Person {name: $to})
                    MERGE (a)-[:ARBEITET_MIT]->(b)
                """, **{"from": name, "to": other})

            for org in p.get("mitglied_von", []):
                session.run("""
                    MATCH (p:Person {name: $person}), (o:Organisation {name: $org})
                    MERGE (p)-[:MITGLIED_VON]->(o)
                """, person=name, org=org)

            for thema in p.get("interessiert_an", []):
                session.run("""
                    MATCH (p:Person {name: $person}), (t:Thema {name: $thema})
                    MERGE (p)-[:INTERESSIERT_AN]->(t)
                """, person=name, thema=thema)

        # Organisation → Projekt (FOERDERT)
        for o in merged_data.get("organisationen", []):
            for proj in o.get("foerdert", []):
                session.run("""
                    MATCH (o:Organisation {name: $org}), (p:Projekt {name: $proj})
                    MERGE (o)-[:FOERDERT]->(p)
                """, org=o["name"], proj=proj)

            for thema in o.get("themen", []):
                session.run("""
                    MATCH (o:Organisation {name: $org}), (t:Thema {name: $thema})
                    MERGE (o)-[:HAT_THEMA]->(t)
                """, org=o["name"], thema=thema)

        # Projekt → Person, Organisation, Thema
        for p in merged_data.get("projekte", []):
            for person in p.get("personen", []):
                session.run("""
                    MATCH (pe:Person {name: $person}), (pr:Projekt {name: $proj})
                    MERGE (pe)-[:ARBEITET_AN]->(pr)
                """, person=person, proj=p["name"])

            for org in p.get("gefoerdert_von", []):
                session.run("""
                    MATCH (o:Organisation {name: $org}), (p:Projekt {name: $proj})
                    MERGE (o)-[:FOERDERT]->(p)
                """, org=org, proj=p["name"])

            for org in p.get("gehoert_zu", []):
                session.run("""
                    MATCH (p:Projekt {name: $proj}), (o:Organisation {name: $org})
                    MERGE (p)-[:GEHOERT_ZU]->(o)
                """, proj=p["name"], org=org)

            for thema in p.get("themen", []):
                session.run("""
                    MATCH (p:Projekt {name: $proj}), (t:Thema {name: $thema})
                    MERGE (p)-[:HAT_THEMA]->(t)
                """, proj=p["name"], thema=thema)

        # Inhaltliche Knoten → Person, Thema, Projekt
        content_types = {
            "erkenntnisse": ("Erkenntnis", "ERKANNT_VON"),
            "entscheidungen": ("Entscheidung", "ENTSCHIEDEN_VON"),
            "meilensteine": ("Meilenstein", "ERREICHT_VON"),
            "herausforderungen": ("Herausforderung", "BETRIFFT_PERSON"),
            "fragen": ("Frage", "BETRIFFT_PERSON"),
        }

        for typ, (label, person_rel) in content_types.items():
            name_field = "text" if typ == "fragen" else "name"
            for item in merged_data.get(typ, []):
                node_name = item.get(name_field, "")
                if not node_name:
                    continue

                for person in item.get("personen", []):
                    session.run(f"""
                        MATCH (n:{label} {{{name_field}: $name}}), (p:Person {{name: $person}})
                        MERGE (n)-[:{person_rel}]->(p)
                    """, name=node_name, person=person)

                for thema in item.get("themen", []):
                    session.run(f"""
                        MATCH (n:{label} {{{name_field}: $name}}), (t:Thema {{name: $thema}})
                        MERGE (n)-[:BETRIFFT_THEMA]->(t)
                    """, name=node_name, thema=thema)

                projekt = item.get("projekt")
                if projekt:
                    session.run(f"""
                        MATCH (n:{label} {{{name_field}: $name}}), (p:Projekt {{name: $proj}})
                        MERGE (n)-[:IN_PROJEKT]->(p)
                    """, name=node_name, proj=projekt)

        # Spannungen → Person
        for s in merged_data.get("spannungen", []):
            name = s.get("name", "")
            if not name:
                continue
            for person in s.get("zwischen_personen", []):
                session.run("""
                    MATCH (s:Spannung {name: $name}), (p:Person {name: $person})
                    MERGE (s)-[:ZWISCHEN]->(p)
                """, name=name, person=person)
            for thema in s.get("themen", []):
                session.run("""
                    MATCH (s:Spannung {name: $name}), (t:Thema {name: $thema})
                    MERGE (s)-[:BETRIFFT_THEMA]->(t)
                """, name=name, thema=thema)


def main():
    parser = argparse.ArgumentParser(description="Neo4j Import v2")
    parser.add_argument("--backup-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-clear", action="store_true", help="Graph nicht vorher löschen")
    args = parser.parse_args()

    if not NEO4J_PASSWORD:
        print("⛔ NEO4J_PASSWORD nicht gesetzt!")
        sys.exit(1)

    print("=== Neo4j Import v2 ===")
    print(f"URI: {NEO4J_URI}")

    driver = connect()
    print("Verbunden.")

    # Backup
    backup_graph(driver)
    if args.backup_only:
        driver.close()
        return

    # Daten laden
    merged_data = {}
    all_types = [
        "personen", "organisationen", "projekte", "themen",
        "erkenntnisse", "entscheidungen", "meilensteine",
        "herausforderungen", "spannungen", "fragen",
    ]

    total_nodes = 0
    for typ in all_types:
        path = MERGED_DIR / f"{typ}.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            merged_data[typ] = data
            count = len(data)
            total_nodes += count
            print(f"  {typ}: {count}")
        else:
            merged_data[typ] = []

    print(f"  GESAMT: {total_nodes} Knoten")

    if args.dry_run:
        print("\n(Dry run — kein Import)")
        driver.close()
        return

    # Graph neu aufbauen
    if not args.no_clear:
        clear_graph(driver)

    print("\nConstraints...")
    create_constraints(driver)

    print("Knoten importieren...")
    import_themen(driver, merged_data.get("themen", []))
    import_personen(driver, merged_data.get("personen", []))
    import_organisationen(driver, merged_data.get("organisationen", []))
    import_projekte(driver, merged_data.get("projekte", []))

    for typ, label, name_field in [
        ("erkenntnisse", "Erkenntnis", "name"),
        ("entscheidungen", "Entscheidung", "name"),
        ("meilensteine", "Meilenstein", "name"),
        ("herausforderungen", "Herausforderung", "name"),
        ("spannungen", "Spannung", "name"),
        ("fragen", "Frage", "text"),
    ]:
        import_content_nodes(driver, merged_data.get(typ, []), label, name_field)

    print("Kanten erstellen...")
    create_edges(driver, merged_data)

    # Statistiken
    with driver.session() as session:
        nodes = session.run("MATCH (n) RETURN count(n) as c").single()["c"]
        edges = session.run("MATCH ()-[r]->() RETURN count(r) as c").single()["c"]
        print(f"\n=== Fertig ===")
        print(f"Graph: {nodes} Knoten, {edges} Kanten")

    driver.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Lokaler Merge v2 — Alias-Normalisierung + Quellen zusammenführen.
Keine API-Calls, rein mechanisch.

Input:  extractions_v2/*.json (Session-, Telegram-, stimme-Extraktionen)
Output: merged_raw/{typ}.json (pro Knotentyp eine Datei)

Usage:
  python3 local_merge_v2.py
"""
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(SCRIPT_DIR.parent)))
EXTRACTIONS_DIR = DATA_DIR / "extractions_v2"
MERGED_DIR = DATA_DIR / "merged_raw"
ALIASES_PATH = SCRIPT_DIR / "aliases.json"

ENTITY_TYPES = [
    "personen", "organisationen", "projekte", "meilensteine",
    "erkenntnisse", "entscheidungen", "herausforderungen",
    "spannungen", "fragen", "themen",
]


def load_aliases():
    """Lädt aliases.json."""
    if not ALIASES_PATH.exists():
        print(f"⚠️ {ALIASES_PATH} nicht gefunden — keine Alias-Normalisierung")
        return {"persons": {}, "projects": {}, "organisations": {}}
    with open(ALIASES_PATH) as f:
        return json.load(f)


def normalize_name(name, alias_map):
    """Wendet Alias-Mapping an. None = löschen."""
    if not isinstance(name, str):
        return name
    lower = name.lower().strip()
    if lower in alias_map:
        return alias_map[lower]  # None = Pseudo-Entität
    return lower


def normalize_name_list(names, alias_map):
    """Normalisiert und dedupliziert eine Namensliste."""
    if not isinstance(names, list):
        return names
    result = []
    for name in names:
        normalized = normalize_name(name, alias_map)
        if normalized is not None and normalized not in result:
            result.append(normalized)
    return result


def split_compound_names(names, alias_map):
    """Splittet 'anton, timo' in ['anton', 'timo']. Prüft Alias für das Kompositum."""
    if not isinstance(names, list):
        return names
    result = []
    for name in names:
        if not isinstance(name, str):
            result.append(name)
            continue
        # Zuerst: ist das Kompositum ein bekannter Alias? (z.B. "anton, timo" → null)
        compound_alias = normalize_name(name, alias_map)
        if compound_alias is None:
            continue  # Pseudo-Entität
        # Dann: enthält es Komma oder "und"?
        if "," in name or " und " in name.lower():
            parts = [p.strip() for p in name.replace(" und ", ",").split(",")]
            for part in parts:
                normalized = normalize_name(part, alias_map)
                if normalized is not None and normalized not in result:
                    result.append(normalized)
        else:
            normalized = normalize_name(name, alias_map)
            if normalized is not None and normalized not in result:
                result.append(normalized)
    return result


def normalize_entity(entity, aliases, entity_type):
    """Normalisiert eine einzelne Entität."""
    if not isinstance(entity, dict):
        return entity

    person_aliases = aliases.get("persons", {})
    project_aliases = aliases.get("projects", {})
    org_aliases = aliases.get("organisations", {})

    # Name normalisieren
    if "name" in entity:
        if entity_type == "personen":
            entity["name"] = normalize_name(entity["name"], person_aliases)
        elif entity_type == "organisationen":
            entity["name"] = normalize_name(entity["name"], org_aliases)
        elif entity_type == "projekte":
            entity["name"] = normalize_name(entity["name"], project_aliases)
        else:
            entity["name"] = entity["name"].lower().strip() if isinstance(entity["name"], str) else entity["name"]

        # null = löschen
        if entity["name"] is None:
            return None

    # text-Feld bei Fragen
    if "text" in entity and isinstance(entity["text"], str):
        entity["text"] = entity["text"].strip()

    # Personen-Listen normalisieren
    for field in ["personen", "kennt", "arbeitet_mit", "zwischen_personen", "mitglieder", "beauftragt"]:
        if field in entity:
            entity[field] = split_compound_names(entity[field], person_aliases)

    # Organisations-Listen
    for field in ["mitglied_von", "gefoerdert_von", "gehoert_zu"]:
        if field in entity:
            entity[field] = normalize_name_list(entity[field], org_aliases)

    # Projekt-Listen
    for field in ["projekt", "foerdert"]:
        if field in entity:
            if isinstance(entity[field], str):
                entity[field] = normalize_name(entity[field], project_aliases)
            elif isinstance(entity[field], list):
                entity[field] = normalize_name_list(entity[field], project_aliases)

    # Themen lowercase
    if "themen" in entity and isinstance(entity["themen"], list):
        entity["themen"] = [t.lower().strip() for t in entity["themen"] if isinstance(t, str)]

    return entity


def main():
    if not EXTRACTIONS_DIR.exists():
        print(f"⛔ {EXTRACTIONS_DIR} nicht gefunden")
        sys.exit(1)

    MERGED_DIR.mkdir(parents=True, exist_ok=True)

    aliases = load_aliases()
    extraction_files = sorted(EXTRACTIONS_DIR.glob("*.json"))
    print(f"Lade {len(extraction_files)} Extraktionsdateien")

    # Sammle alle Entitäten pro Typ
    all_entities = {typ: [] for typ in ENTITY_TYPES}
    sources_seen = set()

    for filepath in extraction_files:
        with open(filepath) as f:
            data = json.load(f)

        meta = data.get("_meta", {})
        quelle = meta.get("quelle", filepath.stem)
        session_id = meta.get("session_id", filepath.stem)
        sources_seen.add(quelle)

        for typ in ENTITY_TYPES:
            items = data.get(typ, [])
            if not isinstance(items, list):
                continue

            for item in items:
                if isinstance(item, str):
                    # Themen als einfache Strings
                    all_entities[typ].append(item)
                    continue

                # Entität normalisieren
                normalized = normalize_entity(item, aliases, typ)
                if normalized is None:
                    continue

                # Quelle anhängen
                if isinstance(normalized, dict):
                    normalized["_quelle"] = quelle
                    if session_id and quelle == "session":
                        normalized.setdefault("session_ids", [])
                        if session_id[:8] not in normalized["session_ids"]:
                            normalized["session_ids"].append(session_id[:8])

                all_entities[typ].append(normalized)

    # Statistiken + speichern
    print(f"\nQuellen: {', '.join(sorted(sources_seen))}")
    print(f"\nEntitäten pro Typ:")

    total = 0
    for typ in ENTITY_TYPES:
        items = all_entities[typ]
        if not items:
            continue

        # Themen: deduplizieren (einfache Strings)
        if typ == "themen":
            items = sorted(set(t.lower().strip() for t in items if isinstance(t, str)))
            all_entities[typ] = items

        count = len(items)
        total += count
        print(f"  {typ:20} {count:4}")

        out_path = MERGED_DIR / f"{typ}.json"
        with open(out_path, "w") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"  {'GESAMT':20} {total:4}")
    print(f"\nGespeichert in: {MERGED_DIR}/")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
stimme/ Markdown-Dateien → strukturiertes JSON für Haiku-Extraktion.
Liest Elis Reflexionen, Briefe, Geschichte und Grundlagendokumente.

Input:  stimme/ Verzeichnis
Output: scripts/stimme_parsed.json

Usage:
  python3 parse_stimme_v2.py /path/to/stimme/
"""
import json
import re
import sys
from pathlib import Path


def extract_date_from_filename(filename):
    """Extrahiert Datum aus Dateiname: 2026-02-01-nachtgedanken.md → 2026-02-01."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def extract_date_from_content(text):
    """Extrahiert Datum aus dem Header: *12. February 2026, 20:01 Uhr*."""
    patterns = [
        r"\*(\d{1,2})\.\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        r"\*(\d{1,2})\.\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(\d{4})",
    ]
    month_map = {
        "January": "01", "February": "02", "March": "03", "April": "04",
        "May": "05", "June": "06", "July": "07", "August": "08",
        "September": "09", "October": "10", "November": "11", "December": "12",
        "Januar": "01", "Februar": "02", "März": "03", "April": "04",
        "Mai": "05", "Juni": "06", "Juli": "07", "August": "08",
        "September": "09", "Oktober": "10", "November": "11", "Dezember": "12",
    }
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month_map[month]}-{day.zfill(2)}"
    return None


def extract_title(text):
    """Extrahiert den Titel (erste # Zeile)."""
    match = re.match(r"#\s+(.+)", text)
    return match.group(1).strip() if match else None


def parse_stimme_dir(stimme_path):
    """Parst alle Markdown-Dateien im stimme/-Verzeichnis."""
    documents = []

    for subdir in ["reflexionen", "briefe", "geschichte"]:
        dir_path = stimme_path / subdir
        if not dir_path.exists():
            continue

        for md_file in sorted(dir_path.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            title = extract_title(content) or md_file.stem
            date = extract_date_from_filename(md_file.name) or extract_date_from_content(content)

            documents.append({
                "typ": {"reflexionen": "reflexion", "briefe": "brief", "geschichte": "geschichte"}[subdir],
                "datei": f"stimme/{subdir}/{md_file.name}",
                "titel": title,
                "datum": date,
                "text": content,
                "zeichen": len(content),
            })

    # Grundlagendokumente im Root
    for root_file in ["anker.md", "auftrag.md", "manifest.md", "fragen.md"]:
        file_path = stimme_path / root_file
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            documents.append({
                "typ": "grundlage",
                "datei": f"stimme/{root_file}",
                "titel": extract_title(content) or root_file.replace(".md", ""),
                "datum": None,
                "text": content,
                "zeichen": len(content),
            })

    return documents


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parse_stimme_v2.py /path/to/stimme/")
        sys.exit(1)

    stimme_path = Path(sys.argv[1])
    if not stimme_path.exists():
        print(f"Verzeichnis nicht gefunden: {stimme_path}")
        sys.exit(1)

    documents = parse_stimme_dir(stimme_path)

    # Statistiken
    total_chars = sum(d["zeichen"] for d in documents)
    types = {}
    for d in documents:
        types[d["typ"]] = types.get(d["typ"], 0) + 1

    print(f"Geparst: {len(documents)} Dokumente")
    print(f"Zeichen gesamt: {total_chars:,}")
    print("Typen:")
    for typ, count in sorted(types.items()):
        print(f"  {typ}: {count}")
    print()
    for d in documents:
        date_str = d["datum"] or "undatiert"
        print(f"  [{date_str}] {d['typ']:12} {d['titel'][:50]}")

    # Output
    output = {
        "quelle": "stimme",
        "person": "eli",
        "dokumente_gesamt": len(documents),
        "zeichen_gesamt": total_chars,
        "documents": documents,
    }

    out_path = Path(__file__).parent / "stimme_parsed.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nGespeichert: {out_path}")


if __name__ == "__main__":
    main()

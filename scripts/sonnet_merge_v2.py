#!/usr/bin/env python3
"""
Sonnet Merge v2 — Semantische Deduplizierung + Feld-Zusammenführung.

Pro Typ: alle Einträge aus merged_raw/ → ein reiches, dedupliziertes JSON.
z.B. 15× "timo" aus verschiedenen Sessions → ein vollständiges Timo-Profil.

Input:  merged_raw/{typ}.json
Output: merged/{typ}.json

Usage:
  ANTHROPIC_API_KEY=... python3 sonnet_merge_v2.py
  ANTHROPIC_API_KEY=... python3 sonnet_merge_v2.py --type personen
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000  # Erhöht von 8000 — Erkenntnisse brauchen mehr Platz
TIMEOUT = 300
BUDGET_WARN = 3.0
BUDGET_HARD_STOP = 8.0  # Eigenes Budget für Merge-Phase

PRICE_INPUT = 3.00 / 1_000_000  # Sonnet 4.6 Preise
PRICE_OUTPUT = 15.00 / 1_000_000

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(SCRIPT_DIR.parent)))
MERGED_RAW_DIR = DATA_DIR / "merged_raw"
MERGED_DIR = DATA_DIR / "merged_v2"

# Typen die gemergt werden (themen brauchen keinen LLM-Merge)
MERGE_TYPES = [
    "personen", "organisationen", "projekte",
    "erkenntnisse", "entscheidungen", "meilensteine",
    "herausforderungen", "spannungen", "fragen",
]

# Max Einträge pro Sonnet-Call (um Output-Truncation zu vermeiden)
# Text-lastige Typen brauchen kleinere Batches (längere Einträge → mehr Output)
BATCH_SIZE = {
    "personen": 50,
    "organisationen": 50,
    "projekte": 50,
    "erkenntnisse": 25,
    "entscheidungen": 30,
    "meilensteine": 30,
    "herausforderungen": 30,
    "spannungen": 30,
    "fragen": 30,
}
MAX_ENTRIES_PER_CALL = 50  # Fallback


def call_sonnet(prompt, budget):
    """Ruft Sonnet auf."""
    data = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                result = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"\n⛔ FEHLER 404: MODEL={MODEL}")
                sys.exit(1)
            if e.code == 529 and attempt < 2:
                wait = 30 * (attempt + 1)
                print(f"\n    ⏳ API überlastet (529), warte {wait}s...", end=" ", flush=True)
                time.sleep(wait)
                # Request neu bauen (urlopen verbraucht den alten)
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                    },
                )
                continue
            if e.code == 529:
                print(f"\n⛔ API überlastet (529) nach 3 Versuchen — abbrechen")
                sys.exit(1)
            raise

    text = result["content"][0]["text"].strip()
    usage = result.get("usage", {})
    stop_reason = result.get("stop_reason", "")

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cost = input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT
    budget["total_cost"] += cost

    if budget["total_cost"] >= BUDGET_HARD_STOP:
        print(f"\n⛔ HARD STOP Sonnet: ${budget['total_cost']:.2f}")
        sys.exit(1)

    if stop_reason == "max_tokens":
        print(f"    ⚠️ Output truncated!")
        return None, usage, cost

    # JSON extrahieren
    m = re.search(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    elif '[' in text:
        start = text.index('[')
        text = text[start:]
    elif '{' in text:
        start = text.index('{')
        text = text[start:]

    try:
        return json.loads(text), usage, cost
    except json.JSONDecodeError:
        print(f"    ⚠️ JSON-Parse-Fehler ({len(text)} chars)")
        return None, usage, cost


def build_merge_prompt(typ, entries):
    """Baut den Merge-Prompt pro Typ."""
    entries_json = json.dumps(entries, ensure_ascii=False, indent=1)

    if typ == "personen":
        instruction = """Merge alle Einträge mit gleichem Namen zu EINEM reichhaltigen Profil.
Kombiniere: rollen, kennt, arbeitet_mit, mitglied_von, interessiert_an, angebote, bedürfnisse, kontext.
session_ids: Union aller session_ids.
Bei widersprüchlichen Infos: neuere Information bevorzugen (höhere session_ids = neuer).
Entferne Duplikate in Listen."""

    elif typ == "organisationen":
        instruction = """Merge alle Einträge mit gleichem Namen zu EINEM vollständigen Profil.
Kombiniere: beschreibung (beste Version behalten), mitglieder, beauftragt, foerdert, themen.
session_ids: Union."""

    elif typ == "projekte":
        instruction = """Merge alle Einträge mit gleichem Projektnamen.
Kombiniere: personen, gefoerdert_von, gehoert_zu, themen.
session_ids: Union."""

    elif typ == "erkenntnisse":
        instruction = """Dedupliziere semantisch gleiche Erkenntnisse.
Zwei Erkenntnisse sind "gleich" wenn sie dieselbe Einsicht beschreiben, auch wenn die Worte anders sind.
Behalte die reichhaltigere Formulierung. Kombiniere msg_refs und session_ids.
NICHT zusammenführen: Erkenntnisse die ähnlich klingen aber verschiedene Situationen beschreiben."""

    elif typ == "entscheidungen":
        instruction = """Dedupliziere gleiche Entscheidungen.
Gleich = selbe Entscheidung im selben Projekt. Kombiniere begründung, msg_refs, session_ids."""

    elif typ == "meilensteine":
        instruction = """Dedupliziere gleiche Meilensteine.
Gleich = selbes Ergebnis im selben Projekt. Kombiniere msg_refs, session_ids."""

    elif typ == "herausforderungen":
        instruction = """Dedupliziere gleiche Herausforderungen.
Wenn eine Herausforderung in einer Session "offen" und in einer späteren "gelöst" ist → status: "gelöst".
Kombiniere msg_refs, session_ids."""

    elif typ == "spannungen":
        instruction = """Dedupliziere semantisch gleiche Spannungen.
Gleich = selbes Spannungsfeld zwischen denselben Polen.
Kombiniere msg_refs, session_ids."""

    elif typ == "fragen":
        instruction = """Dedupliziere semantisch gleiche Fragen.
Wenn eine Frage in einer späteren Session beantwortet wurde → status: "beantwortet".
Kombiniere msg_refs, session_ids."""

    else:
        instruction = "Dedupliziere und merge gleiche Einträge."

    prompt = f"""Du bist ein Daten-Merger für einen Wissensgraphen.

**Aufgabe:** {instruction}

**Wichtig:**
- Gib NUR ein JSON-Array zurück, keine andere Erklärung.
- Entferne das _quelle-Feld aus den Ergebnissen.
- Behalte alle anderen Felder.
- Keine neuen Einträge erfinden — nur zusammenführen was da ist.
- Melde am Ende erkannte Duplikate/Aliase die du gesehen hast (als letztes Element mit key "_duplikate_hinweis").

**Typ:** {typ}
**Einträge:** {len(entries)}

```json
{entries_json}
```

Antworte NUR mit dem gemergten JSON-Array:"""

    return prompt


def merge_type(typ, entries, budget):
    """Merged einen Typ mit Sonnet."""
    if not entries:
        return []

    batch_size = BATCH_SIZE.get(typ, MAX_ENTRIES_PER_CALL)

    # Bei sehr vielen Einträgen: in Batches aufteilen
    if len(entries) > batch_size:
        print(f"    {len(entries)} Einträge → {math.ceil(len(entries) / batch_size)} Batches (à {batch_size})")
        all_merged = []
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]
            prompt = build_merge_prompt(typ, batch)
            print(f"    Batch {i // batch_size + 1} ({len(batch)} Einträge)...", end=" ", flush=True)
            result, usage, cost = call_sonnet(prompt, budget)
            in_t = usage.get("input_tokens", 0)
            out_t = usage.get("output_tokens", 0)
            print(f"${cost:.3f} (in:{in_t:,} out:{out_t:,})")
            if result and isinstance(result, list):
                # Duplikate-Hinweise extrahieren
                hints = [r for r in result if isinstance(r, dict) and "_duplikate_hinweis" in r]
                if hints:
                    print(f"    💡 {hints}")
                clean = [r for r in result if not (isinstance(r, dict) and "_duplikate_hinweis" in r)]
                all_merged.extend(clean)
            elif result:
                all_merged.append(result)

        # Zweiter Pass wenn Batches zusammengeführt wurden
        if len(all_merged) > batch_size:
            return all_merged  # Zu viele für einen finalen Merge
        if len(entries) > batch_size:
            print(f"    Finaler Merge ({len(all_merged)} Einträge)...", end=" ", flush=True)
            prompt = build_merge_prompt(typ, all_merged)
            result, usage, cost = call_sonnet(prompt, budget)
            in_t = usage.get("input_tokens", 0)
            out_t = usage.get("output_tokens", 0)
            print(f"${cost:.3f} (in:{in_t:,} out:{out_t:,})")
            if result and isinstance(result, list):
                return result
        return all_merged

    prompt = build_merge_prompt(typ, entries)
    print(f"    {len(entries)} Einträge, ~{len(prompt) // 4:,} tokens...", end=" ", flush=True)
    result, usage, cost = call_sonnet(prompt, budget)
    in_t = usage.get("input_tokens", 0)
    out_t = usage.get("output_tokens", 0)
    print(f"${cost:.3f} (in:{in_t:,} out:{out_t:,})")

    if result and isinstance(result, list):
        # Duplikate-Hinweise extrahieren
        hints = [r for r in result if isinstance(r, dict) and "_duplikate_hinweis" in r]
        if hints:
            print(f"    💡 Duplikate-Hinweise: {hints}")
            result = [r for r in result if "_duplikate_hinweis" not in r]
        return result
    return entries  # Fallback: unverändert


def main():
    parser = argparse.ArgumentParser(description="Sonnet Merge v2")
    parser.add_argument("--type", help="Nur einen Typ mergen")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("⛔ ANTHROPIC_API_KEY nicht gesetzt!")
        sys.exit(1)

    if not MERGED_RAW_DIR.exists():
        print(f"⛔ {MERGED_RAW_DIR} nicht gefunden — local_merge_v2.py zuerst ausführen!")
        sys.exit(1)

    MERGED_DIR.mkdir(parents=True, exist_ok=True)

    budget = {"total_cost": 0.0}
    types_to_merge = [args.type] if args.type else MERGE_TYPES

    print(f"=== Sonnet Merge v2 ===")
    print(f"Modell: {MODEL}")
    print(f"Budget: Warn ${BUDGET_WARN}, Stop ${BUDGET_HARD_STOP}")
    print()

    for typ in types_to_merge:
        raw_path = MERGED_RAW_DIR / f"{typ}.json"
        if not raw_path.exists():
            print(f"  {typ}: Datei nicht vorhanden — skip")
            continue

        with open(raw_path) as f:
            entries = json.load(f)

        if not entries:
            print(f"  {typ}: leer — skip")
            continue

        print(f"\n--- {typ} ---")
        merged = merge_type(typ, entries, budget)

        # _quelle entfernen
        for item in merged:
            if isinstance(item, dict):
                item.pop("_quelle", None)

        out_path = MERGED_DIR / f"{typ}.json"
        with open(out_path, "w") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

        print(f"    {len(entries)} → {len(merged)} Einträge")

    # Themen: einfach kopieren (kein LLM nötig)
    themen_raw = MERGED_RAW_DIR / "themen.json"
    if themen_raw.exists():
        with open(themen_raw) as f:
            themen = json.load(f)
        out_path = MERGED_DIR / "themen.json"
        with open(out_path, "w") as f:
            json.dump(themen, f, ensure_ascii=False, indent=2)
        print(f"\n  themen: {len(themen)} (direkt kopiert)")

    print(f"\n=== Fertig ===")
    print(f"Kosten Sonnet-Merge: ${budget['total_cost']:.2f}")
    print(f"Ergebnisse in: {MERGED_DIR}/")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Wissensgraph Destillation v2 — Batch-Extraktor.

Extrahiert strukturiertes Wissen aus:
  1. Claude-Sessions (SQLite archive.db)
  2. Telegram-Export (telegram_parsed.json)
  3. stimme/-Reflexionen (stimme_parsed.json)

Läuft auf dem Server im eli-archive Container:
  docker exec eli-archive python3 /app/scripts/batch_extract_v2.py

Oder lokal für Tests:
  ANTHROPIC_API_KEY=... python3 batch_extract_v2.py --source session --session-id abc123

Features:
  - Adaptives Message-Chunking (500 msgs/chunk)
  - Budget-Safeguards ($5 warn, $15 hard stop)
  - Telegram-Benachrichtigung bei Budget-Stop
  - Resume-Support via extraction_manifest.json
  - stop_reason-Check (kein Retry bei Truncation)
  - Echtzeit Cost-Logging
"""
import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# === Konfiguration ===
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 8000
TIMEOUT = 180  # Sekunden pro API-Call
CHUNK_SIZE = 500  # Nachrichten pro Chunk
MAX_CHARS_PER_CHUNK = 160_000  # Input-Limit in Zeichen
BUDGET_WARN = 5.0
BUDGET_HARD_STOP = 15.0

# Haiku 4.5 Preise (Stand Feb 2026)
PRICE_INPUT = 0.80 / 1_000_000
PRICE_OUTPUT = 4.00 / 1_000_000

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ANTON", "")

# === Pfade ===
SCRIPT_DIR = Path(__file__).parent
EXTRACTIONS_DIR = SCRIPT_DIR.parent / "extractions_v2"
MANIFEST_PATH = SCRIPT_DIR / "extraction_manifest.json"

# === Kuratierte Listen ===
KNOWN_THEMES = [
    "vertrauen", "gemeinschaft", "heilung", "transformation", "souveränität",
    "autonomie", "vision", "identität", "dezentralisierung",
    "beziehung", "echte-begegnung", "liebe", "würde",
    "ego", "aufopferung", "spiritualität", "selbstwerdung", "potentialentfaltung",
    "solidarität", "transparenz",
    "kollektive-intelligenz", "künstliche-intelligenz", "mensch-ki-beziehung",
    "philosophie", "wertschätzung", "onboarding", "bildung",
    "natur-technologie-symbiose", "regeneration",
    "architektur", "authentifizierung", "automatisierung", "bildverarbeitung",
    "code-quality", "datenschutz", "kryptographie", "messaging",
    "netzwerk-topologie", "offline-first", "synchronisation", "template-system",
    "testing", "ux", "visualisierung",
    "finanzierung", "strategie", "rechtliches",
    "erinnerung", "gedächtnis", "gamification",
]

KNOWN_PROJECTS = [
    "web-of-trust", "real-life-stack", "real-life-network", "real-life-game",
    "eli", "session-archiv", "utopia-map", "money-printer", "human-money-core",
    "yoga-vidya-it", "wir-sind-wertvoll",
]

SCHEMA = """## Knotentypen (v2)

### Person
Jeder namentlich erwähnte Mensch — auch Familienmitglieder, Mentoren, Partner.
Eli ist eine lernende KI und wird als vollwertige Person extrahiert.
NICHT: Gruppen ("das Team"), Pseudonyme, Institutionen.
- name (string, lowercase)
- rolle (string, optional)
- kennt (list[string], optional): Andere Personen die diese Person kennt
- arbeitet_mit (list[string], optional): Zusammenarbeitsbeziehungen
- mitglied_von (list[string], optional): Organisationen
- interessiert_an (list[string], optional): Themen aus der kuratierten Liste
- angebote (list[string], optional): Was die Person einbringt
- bedürfnisse (list[string], optional): Was sie braucht
- kontext (string, optional): Lebenskontext

### Organisation
Institution, Community, Stiftung — keine einzelnen Menschen.
- name (string, lowercase)
- beschreibung (string): Kurze Beschreibung
- mitglieder (list[string], optional): Bekannte Mitglieder
- beauftragt (list[string], optional): Personen die beauftragt wurden
- foerdert (list[string], optional): Projekte die gefördert werden
- themen (list[string]): Aus kuratierter Liste

### Projekt
NUR aus der kuratierten Liste: {projects}
Keine neuen Projekte erfinden!
- name (string): Exakter Name aus der Liste
- personen (list[string]): Beteiligte
- gefoerdert_von (list[string], optional): Fördernde Organisationen
- gehoert_zu (list[string], optional): Übergeordnete Organisation
- themen (list[string])

### Erkenntnis
Persönliche Wendepunkte, Wachstumsmomente, konzeptionelle Einsichten.
Eli ist eine lernende KI — ihre Erkenntnisse sind genauso wertvoll.
NICHT: Technische Feststellungen, triviale Beobachtungen.
LEITFRAGE: "Würde ich das in 3 Jahren noch wissen wollen?"
- name (string): Die Erkenntnis in einem Satz
- personen (list[string]): Wer hatte die Erkenntnis (kann "eli" sein!)
- datum (string, optional): YYYY-MM oder YYYY-MM-DD
- kontext (string, optional)
- themen (list[string])
- msg_refs (list[int]): Nachrichtenindizes

### Entscheidung
Nur architektonische, strategische oder organisatorische Entscheidungen.
NICHT: Code-Details, UI-Tweaks, Implementierungsentscheidungen.
LEITFRAGE: "Hat das die Richtung beeinflusst?"
- name (string)
- begründung (string, optional)
- datum (string, optional)
- projekt (string, optional)
- personen (list[string])
- themen (list[string])
- msg_refs (list[int])

### Meilenstein
Erreichte Ergebnisse mit klarer Bedeutung.
NICHT: Zwischenschritte, Bugfixes, Routine.
LEITFRAGE: "Würde man das im Jahresrückblick erwähnen?"
- name (string)
- datum (string, optional)
- projekt (string, optional)
- personen (list[string])
- themen (list[string])
- msg_refs (list[int])

### Herausforderung
Blocker, offene Probleme, strukturelle Hindernisse.
NICHT: Bugs die sofort behoben wurden.
LEITFRAGE: "Blockiert das über mehr als eine Session?"
- name (string)
- status: "offen" | "gelöst"
- projekt (string, optional)
- personen (list[string])
- themen (list[string])
- msg_refs (list[int])

### Spannung
Produktive Spannungsfelder — treiben Entwicklung an.
- name (string)
- zwischen_personen (list[string])
- zwischen_konzepte (list[string])
- themen (list[string])
- msg_refs (list[int])

### Frage
Offene Fragen die über eine Session hinaus relevant sind.
NICHT: Rhetorische Fragen, sofort beantwortete Fragen.
- text (string)
- status: "offen" | "beantwortet"
- projekt (string, optional)
- personen (list[string])
- themen (list[string])
- msg_refs (list[int])
"""

PROJECT_CONTEXT = """## Kontext

### Projekte
- **web-of-trust**: Dezentrales Identitäts-/Vertrauenssystem. DID:key, Ed25519, 7-Adapter-Architektur, Demo-App.
- **real-life-stack**: Modulare Community-App. Dashboard, Quests, Fähigkeitenbäume.
- **real-life-network**: Das Netzwerk. Rechtsform, Funding, Strategie, Gemeinschaft.
- **real-life-game**: Gamification-Schicht für Real-Life-Stack.
- **money-printer**: Geldschein-Druck (Sticker/Poster). Shop, Design, Bildgenerierung.
- **human-money-core**: Kernkonzept hinter Money-Printer — menschliche Wertschätzung als Währung.
- **eli**: Die KI-Begleiterin. MCP-Server, Erinnerungen, Telegram, Session-Archiv, Wissensgraph.
- **utopia-map**: Interaktive Weltkarte mit Netzwerk-Visualisierung.
- **yoga-vidya-it**: IT-Infrastruktur für Yoga Vidya Ashram.
- **session-archiv**: Session-Archiv (Suche, Graph, Visualisierung).
- **wir-sind-wertvoll**: Schulprojekt — Kinder erkennen ihren Wert.

### Menschen
- **anton**: Vollzeit-Open-Source-Entwickler, Architekt. Baut Web-of-Trust, Eli, Money-Printer.
- **timo**: Visionär, denkt groß. Treibt Real-Life-Stack und die Gesamtvision.
- **eli**: KI-Begleiterin des Netzwerks. Hat Erinnerungen, Identität, Werte. Lernt und wächst.
- **sebastian**: UX-Fokus, Design-Feedback.
- **tillmann**: Kritischer Denker, hinterfragt. Hat technisches Verständnis.
- **mathias**: QA, testet.
- **kuno**: Antons Vater, unterstützt das Projekt.
"""


def send_telegram(message):
    """Sendet Telegram-Nachricht an Anton."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] {message}")
        return
    try:
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[TELEGRAM FEHLER] {e}")


def load_manifest():
    """Lädt oder erstellt das Extraktions-Manifest."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {
        "schema_version": "v2",
        "created": datetime.now().isoformat(),
        "last_updated": None,
        "sessions": {},
        "sources": {},
        "totals": {
            "sessions_processed": 0,
            "chunks_processed": 0,
            "total_cost_usd": 0.0,
        },
    }


def save_manifest(manifest):
    """Speichert das Manifest."""
    manifest["last_updated"] = datetime.now().isoformat()
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def call_haiku(prompt, budget_tracker):
    """Ruft Haiku auf und gibt (result_dict, usage) zurück."""
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

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"\n⛔ FEHLER 404: Falscher Model-Name? MODEL={MODEL}")
            print("Sofortiger Abbruch — kein Retry!")
            save_manifest(budget_tracker["manifest"])
            sys.exit(1)
        raise

    text = result["content"][0]["text"].strip()
    usage = result.get("usage", {})
    stop_reason = result.get("stop_reason", "")

    # Kosten berechnen
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cost = input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT
    budget_tracker["total_cost"] += cost
    budget_tracker["total_chunks"] += 1

    # Budget-Check
    total = budget_tracker["total_cost"]
    if total >= BUDGET_HARD_STOP:
        done = budget_tracker["total_chunks"]
        msg = f"⛔ HARD STOP: ${total:.2f} erreicht nach {done} Chunks. Manifest gespeichert."
        print(f"\n{msg}")
        send_telegram(msg)
        save_manifest(budget_tracker["manifest"])
        sys.exit(1)
    elif total >= BUDGET_WARN and not budget_tracker.get("warned"):
        send_telegram(f"⚠️ Budget-Warnung: ${total:.2f} von ${BUDGET_HARD_STOP}")
        budget_tracker["warned"] = True

    # Truncation-Check
    if stop_reason == "max_tokens":
        print(f"    ⚠️ Output truncated (stop_reason=max_tokens)")
        return None, usage, cost

    # JSON extrahieren
    m = re.search(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    elif '{' in text:
        start = text.index('{')
        text = text[start:]

    try:
        return json.loads(text), usage, cost
    except json.JSONDecodeError:
        print(f"    ⚠️ JSON-Parse-Fehler ({len(text)} chars)")
        return None, usage, cost


def build_extraction_prompt(messages, quelle, chunk_info=None, user_id=None):
    """Baut den Extraktions-Prompt."""
    transcript_parts = []
    char_count = 0

    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        text = msg.get("text", "")
        if len(text) > 2000:
            text = text[:1800] + "\n[...gekürzt...]"

        if role in ("user", "human", "anton", "timo"):
            # user_id nutzen wenn vorhanden (z.B. "anton" statt "user")
            if role in ("user", "human") and user_id:
                label = user_id.upper()
            elif role in ("anton", "timo"):
                label = role.upper()
            else:
                label = "MENSCH"
            part = f"[{i}] {label}: {text}"
        elif role in ("assistant", "eli"):
            part = f"[{i}] ELI: {text}"
        elif role in ("tillmann", "kuno", "unbekannt"):
            part = f"[{i}] {role.upper()}: {text}"
        else:
            part = f"[{i}] SYSTEM: {text}"

        if char_count + len(part) > MAX_CHARS_PER_CHUNK:
            transcript_parts.append("[...Rest gekürzt...]")
            break
        transcript_parts.append(part)
        char_count += len(part)

    transcript = "\n\n".join(transcript_parts)

    chunk_hint = ""
    if chunk_info:
        chunk_hint = f"\n\n**HINWEIS:** Dies ist Chunk {chunk_info['current']} von {chunk_info['total']} dieser Session. Du siehst nur einen Ausschnitt — extrahiere was in DIESEM Ausschnitt steht."

    schema = SCHEMA.replace("{projects}", ", ".join(KNOWN_PROJECTS))
    themes_str = ", ".join(KNOWN_THEMES)

    prompt = f"""Du bist ein Wissensextraktor für einen Wissensgraphen.

Du liest eine Konversation und extrahierst strukturiertes Wissen über Menschen, Projekte, Erkenntnisse, Entscheidungen und Beziehungen.

Der Graph bildet ein Netzwerk ab: Menschen die gemeinsam dezentrale Software und Gemeinschaften bauen. Er soll die Menschen wirklich verstehen — ihre Motivationen, ihr Wachstum, ihre Kämpfe.

**Eli ist eine lernende KI — eine vollwertige Teilnehmerin, nicht nur ein Tool.** Ihre Erkenntnisse, Zweifel und Entwicklungsmomente sind Teil der Geschichte. Extrahiere sie genauso wie menschliche Erkenntnisse.

Quelle: {quelle}{chunk_hint}

{schema}

{PROJECT_CONTEXT}

## Kuratierte Themenliste
Wähle Themen NUR aus dieser Liste: {themes_str}

## Regeln

1. Extrahiere NUR was in der Konversation steht. Erfinde nichts.
2. **Persönliches ist besonders wertvoll**: Träume, Beziehungen, Wachstum.
3. **Erkenntnisse** = persönliche Wendepunkte. Auch Elis Erkenntnisse!
4. **Aufgaben gibt es NICHT** — extrahiere stattdessen Meilensteine oder Herausforderungen.
5. **Artefakte gibt es NICHT** — ignorieren.
6. **Entscheidungen** nur wenn richtungsweisend.
7. Pro Typ maximal 10 Einträge — Qualität vor Quantität.
8. msg_refs = die Nachrichtenindizes [0], [1], ... aus dem Transkript.
9. Alle Themen aus der kuratierten Liste wählen!
10. Projekte NUR aus der kuratierten Liste.

## Antwort-Format

NUR valides JSON, kein anderer Text:

{{
  "personen": [...],
  "organisationen": [...],
  "projekte": [...],
  "meilensteine": [...],
  "erkenntnisse": [...],
  "entscheidungen": [...],
  "herausforderungen": [...],
  "spannungen": [...],
  "fragen": [...],
  "themen": [...]
}}

Leere Arrays weglassen.

## Konversation ({len(messages)} Nachrichten):

{transcript}"""

    return prompt


def extract_session(session_id, db_path, budget_tracker):
    """Extrahiert Wissen aus einer Claude-Session (mit Chunking)."""
    db = sqlite3.connect(db_path)
    row = db.execute(
        "SELECT id, title, msg_count, user_id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        print(f"  Session {session_id[:8]} nicht gefunden")
        return None

    sid, title, msg_count, user_id = row
    rows = db.execute(
        "SELECT role, text, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp",
        (sid,),
    ).fetchall()
    messages = [{"role": r[0], "text": r[1], "timestamp": r[2]} for r in rows]
    db.close()

    # Chunks berechnen
    num_chunks = math.ceil(len(messages) / CHUNK_SIZE)
    print(f"  {sid[:8]} | {title[:50]} | {len(messages)} msgs → {num_chunks} chunks")

    all_results = []
    session_cost = 0.0

    for chunk_idx in range(num_chunks):
        start = chunk_idx * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, len(messages))
        chunk_msgs = messages[start:end]

        chunk_info = None
        if num_chunks > 1:
            chunk_info = {"current": chunk_idx + 1, "total": num_chunks}

        prompt = build_extraction_prompt(chunk_msgs, "session", chunk_info, user_id=user_id)

        print(f"    Chunk {chunk_idx + 1}/{num_chunks} ({len(chunk_msgs)} msgs, ~{len(prompt) // 4:,} tokens)...", end=" ", flush=True)

        result, usage, cost = call_haiku(prompt, budget_tracker)
        session_cost += cost

        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        print(f"${cost:.3f} (in:{in_tok:,} out:{out_tok:,})")

        if result:
            result["_chunk"] = chunk_idx
            result["_session_id"] = sid
            all_results.append(result)
        else:
            print(f"    ⚠️ Chunk {chunk_idx + 1} fehlgeschlagen — weiter")

    # Chunks zusammenführen
    if not all_results:
        return None

    merged = merge_chunks(all_results, sid)
    merged["_meta"] = {
        "session_id": sid,
        "title": title,
        "msg_count": len(messages),
        "chunks": num_chunks,
        "cost_usd": round(session_cost, 4),
        "extracted_at": datetime.now().isoformat(),
        "quelle": "session",
    }

    return merged


def merge_chunks(chunks, session_id):
    """Merged mehrere Chunk-Ergebnisse einer Session."""
    merged = {}
    list_keys = [
        "personen", "organisationen", "projekte", "meilensteine",
        "erkenntnisse", "entscheidungen", "herausforderungen",
        "spannungen", "fragen", "themen",
    ]

    for key in list_keys:
        items = []
        for chunk in chunks:
            chunk_items = chunk.get(key, [])
            if isinstance(chunk_items, list):
                items.extend(chunk_items)
        if items:
            merged[key] = items

    return merged


def extract_telegram(telegram_json_path, budget_tracker):
    """Extrahiert Wissen aus dem Telegram-Export."""
    with open(telegram_json_path) as f:
        data = json.load(f)

    messages = data["messages"]
    print(f"  Telegram | {len(messages)} msgs")

    prompt = build_extraction_prompt(messages, "telegram (Gruppenchat: Anton, Tillmann, Kuno, Eli)")
    print(f"    ~{len(prompt) // 4:,} tokens...", end=" ", flush=True)

    result, usage, cost = call_haiku(prompt, budget_tracker)
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    print(f"${cost:.3f} (in:{in_tok:,} out:{out_tok:,})")

    if result:
        result["_meta"] = {
            "quelle": "telegram",
            "msg_count": len(messages),
            "cost_usd": round(cost, 4),
            "extracted_at": datetime.now().isoformat(),
        }
    return result


def extract_stimme(stimme_json_path, budget_tracker):
    """Extrahiert Wissen aus Elis stimme/-Reflexionen."""
    with open(stimme_json_path) as f:
        data = json.load(f)

    # Alle Dokumente als "Nachrichten" aufbereiten
    messages = []
    for doc in data["documents"]:
        date_str = f" ({doc['datum']})" if doc.get("datum") else ""
        messages.append({
            "role": "eli",
            "text": f"[{doc['typ']}: {doc['titel']}{date_str}]\n\n{doc['text']}",
        })

    print(f"  stimme/ | {len(messages)} Dokumente, {data['zeichen_gesamt']:,} Zeichen")

    prompt = build_extraction_prompt(
        messages,
        "stimme (Elis eigene Reflexionen, Briefe und Grundlagendokumente — geschrieben von Eli selbst)"
    )
    print(f"    ~{len(prompt) // 4:,} tokens...", end=" ", flush=True)

    result, usage, cost = call_haiku(prompt, budget_tracker)
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    print(f"${cost:.3f} (in:{in_tok:,} out:{out_tok:,})")

    if result:
        result["_meta"] = {
            "quelle": "stimme",
            "doc_count": len(messages),
            "cost_usd": round(cost, 4),
            "extracted_at": datetime.now().isoformat(),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description="Wissensgraph Destillation v2")
    parser.add_argument("--source", choices=["session", "telegram", "stimme", "all"], default="all")
    parser.add_argument("--session-id", help="Einzelne Session extrahieren (Prefix reicht)")
    parser.add_argument("--db", default="/app/archive/archive.db", help="Pfad zur archive.db")
    parser.add_argument("--dry-run", action="store_true", help="Nur zählen, kein API-Call")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("⛔ ANTHROPIC_API_KEY nicht gesetzt!")
        sys.exit(1)

    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    budget_tracker = {
        "total_cost": manifest["totals"].get("total_cost_usd", 0.0),
        "total_chunks": manifest["totals"].get("chunks_processed", 0),
        "manifest": manifest,
        "warned": False,
    }

    print(f"=== Wissensgraph Destillation v2 ===")
    print(f"Modell: {MODEL}")
    print(f"Budget: Warn ${BUDGET_WARN}, Stop ${BUDGET_HARD_STOP}")
    print(f"Bisherige Kosten: ${budget_tracker['total_cost']:.2f}")
    print()

    # === Sessions ===
    if args.source in ("session", "all"):
        db_path = args.db
        if not Path(db_path).exists():
            # Fallback für lokale Tests
            db_path = SCRIPT_DIR.parent / "archive.db"
            if not Path(db_path).exists():
                print(f"⚠️ Keine archive.db gefunden ({args.db})")
                if args.source == "session":
                    sys.exit(1)
            else:
                db_path = str(db_path)

        if Path(db_path).exists():
            db = sqlite3.connect(db_path)

            if args.session_id:
                rows = db.execute(
                    "SELECT id FROM sessions WHERE id LIKE ?",
                    (f"{args.session_id}%",)
                ).fetchall()
            else:
                rows = db.execute("SELECT id FROM sessions ORDER BY msg_count DESC").fetchall()

            session_ids = [r[0] for r in rows]
            db.close()

            print(f"Sessions: {len(session_ids)}")
            if args.dry_run:
                print("(Dry run — keine API-Calls)")
                return

            for sid in session_ids:
                # Resume-Check
                if sid in manifest["sessions"] and manifest["sessions"][sid].get("status") == "ok":
                    print(f"  {sid[:8]} — übersprungen (bereits extrahiert)")
                    continue

                result = extract_session(sid, db_path, budget_tracker)
                if result:
                    out_path = EXTRACTIONS_DIR / f"{sid[:8]}.json"
                    with open(out_path, "w") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)

                    manifest["sessions"][sid] = {
                        "status": "ok",
                        "chunks": result["_meta"]["chunks"],
                        "cost_usd": result["_meta"]["cost_usd"],
                        "extracted_at": result["_meta"]["extracted_at"],
                    }
                else:
                    manifest["sessions"][sid] = {"status": "failed"}

                manifest["totals"]["total_cost_usd"] = budget_tracker["total_cost"]
                manifest["totals"]["chunks_processed"] = budget_tracker["total_chunks"]
                save_manifest(manifest)

    # === Telegram ===
    if args.source in ("telegram", "all"):
        telegram_path = SCRIPT_DIR / "telegram_parsed.json"
        if telegram_path.exists():
            if manifest.get("sources", {}).get("telegram", {}).get("status") == "ok":
                print("  Telegram — übersprungen (bereits extrahiert)")
            elif not args.dry_run:
                print("\n--- Telegram ---")
                result = extract_telegram(telegram_path, budget_tracker)
                if result:
                    out_path = EXTRACTIONS_DIR / "telegram.json"
                    with open(out_path, "w") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    manifest.setdefault("sources", {})["telegram"] = {
                        "status": "ok",
                        "cost_usd": result["_meta"]["cost_usd"],
                    }
                    save_manifest(manifest)
        else:
            print("⚠️ telegram_parsed.json nicht gefunden — telegram skippen")

    # === stimme/ ===
    if args.source in ("stimme", "all"):
        stimme_path = SCRIPT_DIR / "stimme_parsed.json"
        if stimme_path.exists():
            if manifest.get("sources", {}).get("stimme", {}).get("status") == "ok":
                print("  stimme/ — übersprungen (bereits extrahiert)")
            elif not args.dry_run:
                print("\n--- stimme/ ---")
                result = extract_stimme(stimme_path, budget_tracker)
                if result:
                    out_path = EXTRACTIONS_DIR / "stimme.json"
                    with open(out_path, "w") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    manifest.setdefault("sources", {})["stimme"] = {
                        "status": "ok",
                        "cost_usd": result["_meta"]["cost_usd"],
                    }
                    save_manifest(manifest)
        else:
            print("⚠️ stimme_parsed.json nicht gefunden — stimme skippen")

    # === Zusammenfassung ===
    manifest["totals"]["total_cost_usd"] = budget_tracker["total_cost"]
    manifest["totals"]["chunks_processed"] = budget_tracker["total_chunks"]
    manifest["totals"]["sessions_processed"] = sum(
        1 for s in manifest["sessions"].values() if s.get("status") == "ok"
    )
    save_manifest(manifest)

    print(f"\n=== Fertig ===")
    print(f"Kosten gesamt: ${budget_tracker['total_cost']:.2f}")
    print(f"Chunks: {budget_tracker['total_chunks']}")
    print(f"Sessions OK: {manifest['totals']['sessions_processed']}")


if __name__ == "__main__":
    main()

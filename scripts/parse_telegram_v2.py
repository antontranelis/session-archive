#!/usr/bin/env python3
"""
Telegram HTML-Export → strukturiertes JSON für Haiku-Extraktion.
Parst den ChatExport von Telegram Desktop.

Input:  ChatExport_2026-02-27/messages.html
Output: scripts/telegram_parsed.json

Usage:
  python3 parse_telegram_v2.py /path/to/ChatExport_2026-02-27/messages.html
"""
import json
import re
import sys
from pathlib import Path


def parse_timestamp(ts_str):
    """Parst Telegram-Timestamp '28.07.2025 01:29:21 GMT+01:00' → ISO."""
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})", ts_str)
    if match:
        d, m, y, h, mi, s = match.groups()
        return f"{y}-{m}-{d}T{h}:{mi}:{s}"
    return ts_str


def normalize_from(name):
    """Normalisiert Absendernamen."""
    name = name.strip()
    mapping = {
        "Anton ✨": "anton",
        "Anton": "anton",
        "Eli": "eli",
        "Tillmann": "tillmann",
        "Kuno": "kuno",
        "Deleted Account": "unbekannt",
    }
    return mapping.get(name, name.lower())


def strip_html(text):
    """Entfernt HTML-Tags, konvertiert <br> zu Newlines."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"')
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&laquo;", "«")
    text = text.replace("&raquo;", "»")
    return text.strip()


def parse_messages(html):
    """Regex-basierter Parser — robuster als HTMLParser für Telegram-Export."""
    messages = []
    last_from = None

    # Splitte in message-divs
    # Jede Nachricht beginnt mit <div class="message default clearfix [joined]"
    msg_pattern = re.compile(
        r'<div class="message default clearfix\s*(joined)?" id="message(\d+)">(.*?)(?=<div class="message |</div>\s*</div>\s*</div>\s*$)',
        re.DOTALL,
    )

    # Alternative: Splitte anhand der message-IDs
    parts = re.split(r'(<div class="message default clearfix)', html)

    current_from = None
    for i, part in enumerate(parts):
        if '<div class="message default clearfix' not in part:
            if i + 1 < len(parts):
                continue
            else:
                continue

        # Kombiniere den Marker mit dem nächsten Teil
        if i + 1 < len(parts):
            block = part + parts[i + 1]
        else:
            continue

        is_joined = "joined" in block[:100]

        # Datum extrahieren
        date_match = re.search(r'date details" title="([^"]+)"', block)
        timestamp = date_match.group(1) if date_match else ""

        # Absender extrahieren (nur bei nicht-joined)
        from_match = re.search(
            r'class="from_name">\s*(.*?)\s*</div>', block, re.DOTALL
        )
        if from_match:
            raw_name = strip_html(from_match.group(1)).strip()
            # Datum-Suffix entfernen falls vorhanden
            raw_name = re.sub(
                r"\s+\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}$", "", raw_name
            )
            current_from = raw_name
        elif is_joined and last_from:
            current_from = last_from

        # Text extrahieren
        text_match = re.search(
            r'<div class="text">(.*?)</div>\s*</div>', block, re.DOTALL
        )
        if not text_match:
            # Fallback: suche nach text div mit mehr Kontext
            text_match = re.search(r'<div class="text">(.*?)\n\s*</div>', block, re.DOTALL)

        if text_match and current_from:
            text = strip_html(text_match.group(1))
            if text and len(text) > 3:
                messages.append(
                    {
                        "from": current_from,
                        "text": text,
                        "timestamp": timestamp,
                    }
                )
                last_from = current_from

    return messages


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parse_telegram_v2.py <messages.html>")
        sys.exit(1)

    html_path = Path(sys.argv[1])
    if not html_path.exists():
        print(f"Datei nicht gefunden: {html_path}")
        sys.exit(1)

    with open(html_path, encoding="utf-8") as f:
        content = f.read()

    raw_messages = parse_messages(content)

    # Normalisieren
    messages = []
    for msg in raw_messages:
        normalized = {
            "role": normalize_from(msg["from"]),
            "text": msg["text"],
            "timestamp": parse_timestamp(msg["timestamp"]),
        }
        if normalized["text"] and len(normalized["text"]) > 5:
            messages.append(normalized)

    # Statistiken
    authors = {}
    for m in messages:
        authors[m["role"]] = authors.get(m["role"], 0) + 1

    dates = [m["timestamp"][:10] for m in messages if m["timestamp"]]
    first_date = min(dates) if dates else "?"
    last_date = max(dates) if dates else "?"

    print(f"Geparst: {len(messages)} Nachrichten")
    print(f"Zeitraum: {first_date} — {last_date}")
    print("Autoren:")
    for author, count in sorted(authors.items(), key=lambda x: -x[1]):
        print(f"  {author}: {count}")

    # Output
    output = {
        "quelle": "telegram",
        "gruppe": "Anton, Tillmann, Kuno, Eli",
        "zeitraum": {"von": first_date, "bis": last_date},
        "nachrichten_gesamt": len(messages),
        "autoren": authors,
        "messages": messages,
    }

    out_path = Path(__file__).parent / "telegram_parsed.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nGespeichert: {out_path}")


if __name__ == "__main__":
    main()

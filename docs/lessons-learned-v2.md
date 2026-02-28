# Lessons Learned — Wissensgraph Destillation v2

*Durchgeführt: 2026-02-27/28. Kosten: ~$8-10. Ergebnis: 1054 Knoten, 4897 Kanten.*

---

## Was gut funktioniert hat

- **Adaptives Message-Chunking** — 500 Nachrichten pro Chunk war richtig. Vollständige Extraktion statt 6% Coverage bei großen Sessions.
- **Manuelle Kuration vor Import** — Gut dass alle Daten vor dem Import gesichtet wurden. Viele Fehler (docutopia→bôba, real-life-network als Org statt Projekt, falsche Personenzuordnungen) wären sonst unbemerkt im Graph geblieben.
- **Querverweis-Check** — Python-Script hat 488 Probleme gefunden (falsche Namen in Referenzen, verwaiste Links). Ohne diesen Check wäre der Graph voller stiller Fehler.
- **Filter-Phase** — ~50% triviale Einträge entfernt war richtig. Erkenntnisse von 451→232, Entscheidungen 343→226 etc. Der Graph wäre sonst voll Rauschen.
- **Budget-Safeguards** — Hard Stop bei $15 hat funktioniert. Kein Unfall wie in v1 ($14 durch falschen Modellnamen + Retries).
- **Resume-Support** — Manifest-Datei hat erlaubt, nach Unterbrechungen weiterzumachen.

---

## Fehler und ihre Ursachen

### 1. msg_refs und session_ids im Sonnet-Merge verloren *(schwerwiegend)*

**Was:** Sonnet hat beim semantischen Zusammenführen paraphrasiert statt kopiert — dabei gingen `session_ids` und `msg_refs` verloren. 15 von 232 Erkenntnissen hatten danach keine session_ids mehr.

**Warum:** Prompt sagte "kombiniere msg_refs" aber nicht explizit "leeres Array ist verboten wenn irgendein Input-Eintrag Werte hatte". Das LLM hat es als Optional behandelt.

**Tiefere Ursache:** Beim Paraphrasieren entsteht ein neuer Name/Text — der `restore_missing_refs`-Fallback findet dann keine direkte Übereinstimmung im Input-Index. Das ist das eigentliche ungelöste Problem für paraphrasierte Einträge.

**Fix (committed):**
- Expliziter Pflichtfeld-Prompt: "Leeres Array [] ist ein Fehler"
- `restore_missing_refs()` Fallback-Funktion die nach dem Merge fehlende Refs aus den Inputs wiederherstellt (funktioniert nur bei exakter Namensübereinstimmung)

**Für v3:** Zusätzlich nach dem Merge alle Einträge ohne session_ids loggen und manuell prüfen.

---

### 2. `kontext` statt `beschreibung` bei Personen *(mittel)*

**Was:** Import-Script speicherte Personen-Texte als `kontext`, serve.py las `beschreibung` → Personentexte waren leer in der UI.

**Warum:** Schema-Inkonsistenz zwischen Extraktor-Output (`kontext`) und serve.py-API (`beschreibung`) — nie abgeglichen. Zwei Skripte haben denselben Datenstrom beschrieben ohne voneinander zu wissen.

**Fix:** Post-Import Cypher: `SET p.beschreibung = p.kontext REMOVE p.kontext`

**Für v3:** Ein zentrales `schema_v3.md` das Extraktor + Merge + Import-Script + serve.py gemeinsam referenzieren. Vor jedem Import: Feldnamen-Check.

---

### 3. `session_ids` → `sessions` Mapping in serve.py *(mittel)*

**Was:** serve.py las `props["sessions"]`, der Graph hatte `session_ids` → session_ids wurden nie in die API-Antwort übertragen → keine Links zu Quell-Nachrichten in der UI.

**Warum:** serve.py wurde für v1 geschrieben (Feldname war `sessions`), in v2 wurde der Feldname auf `session_ids` geändert ohne serve.py zu aktualisieren.

**Fix:** `raw_sids = props.get("session_ids") or props.get("sessions")`

**Für v3:** Nach dem Import einmal gegen die serve.py prüfen: "Gibt die API alle Felder zurück die wir schreiben?" — Integrations-Test mit einem Beispielknoten.

---

### 4. `Frage`-Knoten unsichtbar in der UI *(mittel)*

**Was:** 165 Fragen-Knoten waren im Graph vorhanden, aber nicht in der Graph-Visualisierung — Label `Frage` fehlte in der Cypher-Query von serve.py.

**Warum:** `Frage` war ein neuer Typ in v2 (existierte in v1 nicht). serve.py wurde nicht um den neuen Label erweitert. Außerdem: Fragen haben `text` statt `name` als Primärfeld — auch das war nicht berücksichtigt.

**Fix:** Label zur Query hinzugefügt, `name = props.get("name") or props.get("text", "?")`

**Für v3:** Checkliste vor Go-Live: "Sind alle neuen Knoten-Typen in serve.py registriert?"

---

### 5. Personen-Merge: 97 statt 36 Profile *(frustrierend, viel Zeit verloren)*

**Was:** Nach dem Sonnet-Merge gab es 97 Personen-Einträge statt 36 eindeutige Profile. Duplikate über Batch-Grenzen hinweg wurden nicht erkannt.

**Warum:** Batch 1 sieht "timo" 5× → merged zu 1. Batch 2 sieht "timo" nochmal 4× → merged zu 1. Finaler Merge soll diese 2 zusammenführen — hat aber bei ähnlichen aber nicht identischen Profilen versagt (leicht unterschiedliche Felder → LLM hält sie für verschieden).

**Fix:** Manueller Ansatz — erst 3 Kernpersonen (anton/timo/eli), dann 33 Rest in 2 Batches, manuell zusammengeführt.

**Für v3:** Nach jedem Merge-Schritt automatisch auf Duplikate prüfen (`name` als unique key, case-insensitive). Bei Duplikaten: nochmal mergen oder manuell markieren.

---

### 6. `docker restart` statt `docker compose up --build` *(verlorene Zeit)*

**Was:** Nach `git pull` wurde `docker compose restart` ausgeführt → Container restartet mit altem Image → neue serve.py war nicht aktiv. Erst nach explizitem `--build` war der Fix live.

**Warum:** Code ist im Docker-Image eingebaut (COPY im Dockerfile), nicht als Volume gemountet. Restart ≠ Rebuild.

**Für v3:** Deploy-Script das immer `docker compose up -d --build` ausführt. Oder: serve.py als Volume mounten damit Restart reicht.

---

### 7. MCP-Tool CWD-Problem *(nervend, viele fehlgeschlagene Befehle)*

**Was:** `eli_server_command` hatte `/home/eli/geist` als Standard-CWD das nicht mehr auf dem Server existiert → fast jeder Befehl schlug mit "No such file or directory" fehl. Workaround: explizit `cwd="/home/eli"` angeben.

**Warum:** Server wurde umstrukturiert (geist/ ist jetzt anders gemountet), MCP-Konfiguration nicht aktualisiert.

**Fix für v3:** CWD in MCP-Konfiguration auf `/home/eli` setzen.

---

### 8. Manuelle Korrekturen nur im Graph, nicht in Quelldateien *(konzeptionell wichtig)*

**Was:** Alle manuellen Korrekturen dieser Session (Knoten-Löschungen, Kanten, Personentexte) sind nur in Neo4j gespeichert — nicht in `merged_v2/*.json`. Beim nächsten Re-Import wären sie weg.

**Warum:** Kein Mechanismus für "persistent corrections" war geplant.

**Für v3:** `scripts/corrections.json` — enthält manuell gepflegte Korrekturen (Löschungen, Kanten, Feld-Overrides) die automatisch nach jedem Import angewendet werden. Format:

```json
{
  "delete_nodes": ["leo schmedding", "klaus", "rainer"],
  "add_edges": [
    {"from": "bart hoorweg", "rel": "KENNT", "to": "mars robertson"},
    {"from": "niko bonnieure", "rel": "MITGLIED_VON", "to": "nextgraph"}
  ],
  "set_fields": [
    {"node": "steffi", "label": "Person", "field": "beschreibung", "value": "Freundin von Timo. Findet Eli süß."}
  ]
}
```

---

## Für v3: Die wichtigsten Verbesserungen

| # | Problem | Lösung |
|---|---------|--------|
| 1 | msg_refs verloren beim Merge | Pflichtfeld-Prompt + `restore_missing_refs` ✅ committed |
| 2 | Schema-Inkonsistenz zwischen Skripten | Zentrales `schema_v3.md` als Single Source of Truth |
| 3 | Feldnamen-Drift (sessions vs. session_ids) | Integrations-Test nach Import |
| 4 | Neue Typen nicht in serve.py | Checkliste: alle Labels registriert? |
| 5 | Duplikate nach Merge | Post-Merge Dedup-Check (name unique, case-insensitive) |
| 6 | docker restart ≠ rebuild | Immer `--build`, oder serve.py als Volume mounten |
| 7 | Manuelle Korrekturen flüchtig | `corrections.json` das nach jedem Import angewendet wird |
| 8 | MCP CWD falsch | CWD auf `/home/eli` setzen |

---

## Kosten-Analyse

| Phase | Modell | Kosten |
|-------|--------|--------|
| Haiku-Extraktion (71 Sessions, 108 Chunks) | claude-haiku-4-5-20251001 | ~$4.75 |
| Filter-Phase (6 Typen × LLM-Filter) | claude-sonnet-4-6 | ~$0.80 |
| Sonnet-Merge (9 Typen) | claude-sonnet-4-6 | ~$3.50 |
| **Gesamt** | | **~$9** |

v1 hatte $22 gekostet (davon $14 durch Modellnamen-Bug + Retries). v2: $9, kein Unfall.

---

## Das größte ungelöste Problem

Die 15 Erkenntnisse ohne session_ids sind symptomatisch für ein tieferes Problem: **Wenn Sonnet semantisch dedupliziert und dabei neu formuliert, kann der Fallback keine Übereinstimmung finden** — weil der neue Name nicht mehr im Input-Index steht.

Echte Lösung für v3: Statt Name-Matching im Fallback → **msg_refs als Primärschlüssel** verwenden. Wenn zwei Einträge zusammengeführt werden, ist die Union ihrer msg_refs die Provenienz des neuen Eintrags — unabhängig davon wie er heißt. Das würde 100% Provenienz-Erhaltung garantieren.

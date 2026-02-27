# Wissensgraph — Änderungsliste für Destillation v2

Erstellt nach Auswertung des ersten Graph-Wurfs (Februar 2026).

## Befund

- **32 von 57 Personen** komplett verwaist (keine Kanten)
- **40 von 40 Organisationen** komplett verwaist (null Kanten zu irgendetwas)
- Themen haben keine Sessions → können nicht mit Personen/Projekten verknüpft werden
- Nur 14 Projekt-Knoten, obwohl ~30+ Projektnamen in den Daten vorkommen
- Pseudo-Personen wie "anton, timo" entstehen beim Merge

---

## Änderungen nach Priorität

### [P0] Organisationen — strukturierte Verbindungsfelder

**Problem:** Alle 40 Organisationen haben null Kanten. `beziehung` ist Freitext, wird nicht ausgewertet.

**Lösung:** Neue strukturierte Felder im Destillations-Prompt:
```json
{
  "name": "yoga vidya e.v.",
  "mitglieder":   ["sukadev", "shankari"],
  "beauftragt":   ["anton"],
  "foerdert":     ["yoga-vidya-it"],
  "themen":       ["yoga", "it-infrastruktur", "gemeinschaft"],
  "beschreibung": "..."
}
```

Neue Kanten:
- `(Person)-[:MITGLIED_VON]->(Organisation)`
- `(Person)-[:BEAUFTRAGT_VON]->(Organisation)`
- `(Organisation)-[:FOERDERT]->(Projekt)`
- `(Organisation)-[:HAT_THEMA]->(Thema)`

---

### [P0] Personen — explizite Beziehungsfelder

**Problem:** 32 von 57 Personen verwaist. Beziehungen zwischen Menschen fehlen fast komplett.

**Lösung:** Neue Felder:
```json
{
  "name": "timo",
  "kennt":        ["anton", "tillmann", "sebastian"],
  "arbeitet_mit": ["anton", "eli"],
  "mitglied_von": ["it4change", "prototype fund"]
}
```

Neue Kanten:
- `(Person)-[:KENNT]->(Person)`
- `(Person)-[:ARBEITET_MIT]->(Person)`
- `(Person)-[:MITGLIED_VON]->(Organisation)`

---

### [P1] Themen — Sessions und Verbindungen hinzufügen

**Problem:** `themen.json` hat nur `name` + `count`, keine Sessions → keine Verknüpfungsmöglichkeit.

**Lösung:** Themen mit Sessions aus SQLite-Tags anreichern:
```json
{
  "name": "dezentralisierung",
  "count": 66,
  "sessions":       ["061d22f6", ...],
  "hauptpersonen":  ["anton", "timo"],
  "hauptprojekte":  ["web-of-trust", "real-life-stack"]
}
```

Neue Kanten:
- `(Person)-[:INTERESSIERT_AN]->(Thema)`
- `(Projekt)-[:HAT_THEMA]->(Thema)` (aktuell nur 6×, sollte ~100+ sein)

---

### [P1] Projekte — erweiterte Liste vorher kuratieren

**Problem:** Nur 14 Projekt-Knoten, aber ~30+ Projektnamen stecken als Strings in Aufgaben, Erkenntnissen etc. Außerdem sind manche Projektnamen inkonsistent (Schreibweise, Duplikate) oder veraltet.

**Lösung:** Vor der Destillation die Projektliste manuell kuratieren:

- Alle eindeutigen Werte aus `aufgaben.projekt`, `erkenntnisse.projekt`, `meilensteine.projekt` etc. zusammenführen
- Duplikate und Varianten bereinigen (z.B. "web-of-trust" vs "wot" vs "Web of Trust")
- Veraltete oder irrelevante Projekte entfernen
- Fehlende wichtige Projekte ergänzen
- Danach als Basis für die Destillation nutzen — das LLM soll nur aus dieser Liste wählen, keine neuen Projektnamen erfinden

**Reihenfolge:** Projektliste kuratieren → Session-Zusammenfassungen aktualisieren → Destillation starten

---

### [P2] Pseudo-Personen schon im Merge verhindern

**Problem:** LLM extrahiert manchmal Gruppen als eine Person: "anton, timo", "eli/anton".

**Lösung:** Post-Processing direkt im Merge-Script:
- Namen mit `,` oder `/` automatisch splitten
- Als separate Personen-Einträge behandeln
- KENNT-Kanten zwischen den Teilen anlegen
- Nie als zusammengesetzten Knoten speichern

---

### [P2] `spannungen.zwischen` aufteilen

**Problem:** Mischt echte Personen mit abstrakten Konzepten (`'anthropic-interessen'`, `'autonomie'`).

**Lösung:** Im Prompt explizit trennen:
```json
{
  "zwischen_personen":  ["anton", "eli"],
  "zwischen_konzepten": ["autonomie", "kontrolle"]
}
```

Kanten:
- `(Spannung)-[:ZWISCHEN]->(Person)` nur für echte Personen
- `(Spannung)-[:BETRIFFT_KONZEPT]->(Thema)` für abstrakte Pole

---

### [P1] Session-Zusammenfassungen parallel zur Destillation aktualisieren

**Problem:** Viele Session-Zusammenfassungen sind veraltet oder unvollständig.

**Erkenntnis:** Destillation und Zusammenfassung erfordern beide, dass jede Session vollständig gelesen wird — es wäre ineffizient das zweimal zu tun.

**Lösung:** In einem einzigen Durchlauf pro Session beides erzeugen:

```text
für jede Session:
  → Zusammenfassung aktualisieren  (→ SQLite)
  → Entitäten + Relationen extrahieren  (→ merged JSON)
```

Das spart API-Kosten und hält beides konsistent.

---

### [P1] Entscheidungen — Qualität und Personenbezug

**Problem 1 — Qualität:** Viele Entscheidungen sind triviale Implementierungsdetails ohne Wissenswert (z.B. "Black Background für Gemini-Generierung", "Maximal 3 Farben aus Logo extrahieren"). Nur 224 Kanten, alle nur zu Projekten.

**Problem 2 — Personenbezug:** Entscheidungen hängen nur an Projekten, aber nicht an den Personen die sie getroffen haben.

**Lösung:**

- Im Destillations-Prompt klaren Schwellenwert setzen: nur Entscheidungen die **Architektur, Strategie, Richtung oder Werte** betreffen — keine Implementierungsdetails
- Neues Pflichtfeld `entschieden_von: ["anton", "timo"]`
- Neues optionales Feld `alternativen: ["was auch erwogen wurde"]`

Neue Kanten:

- `(Entscheidung)-[:ENTSCHIEDEN_VON]->(Person)`

---

## Offene Fragen

- Welche Kantentypen brauchen wir zwischen Person und Organisation? (MITGLIED_VON, BEAUFTRAGT_VON, GRUENDER_VON, FOERDERNEHMER_VON, ...)
- Sollen Funding-Organisationen (nlnet, prototype fund) anders modelliert werden als Community-Orgs (yoga vidya, rainbow gathering)?
- Wie granular sollen Themen-Verbindungen sein — pro Person oder nur für Hauptpersonen?

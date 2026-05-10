# Wissensgraph — Änderungsliste für Destillation v2

Erstellt nach Auswertung des ersten Graph-Wurfs (Februar 2026).

## Befund

**Knoten (aktuell):** 249 Artefakte · 232 Entscheidungen · 231 Erkenntnisse · 191 Meilensteine · 153 Herausforderungen · 122 Spannungen · 71 Sessions · 63 Themen · 57 Personen · 40 Organisationen · 23+7 Projekte

**Sessions sind kein Graph-Knoten-Typ** — sie sind technische Container für Nachrichten, ein Layer unter dem Graph. Sie erscheinen im Graph nur als Referenz: jeder Knoten trägt `session_id` und `msg_refs` als Properties, die direkt zu den Quell-Nachrichten im Archiv verlinken. Kein `(Session)-[:HAT_THEMA]->` etc. — Sessions werden nicht visualisiert.

**Strukturprobleme:**

- **32 von 57 Personen** komplett verwaist (keine Kanten)
- **40 von 40 Organisationen** komplett verwaist — `beziehung`-Feld ist Freitext, wird nicht ausgewertet
- **6 HAT_THEMA-Kanten** bei 63 Themen-Knoten — Themen fast vollständig isoliert
- **Keine Fragen-Knoten** — offene Fragen fehlen ganz, obwohl im ursprünglichen Plan vorgesehen
- **249 Artefakte** größtenteils Dateisystem-Inventar ohne Personenbezug oder Wissenswert
- **23 `Projekt` + 7 `Project`-Knoten** (englisch) — Schema-Duplikate aus verschiedenen Runden
- **Pseudo-Personen** ("anton, timo", "eli/anton") entstehen beim Merge
- **Brücken-Abfrage Anton↔Timo liefert nichts** — obwohl beide seit Jahren zusammenarbeiten

**Inhaltsprobleme:**

- Entscheidungen, Erkenntnisse, Meilensteine, Herausforderungen hängen nur an Projekten, nicht an Menschen
- Großteil der Entscheidungen: triviale Implementierungsdetails ("Black Background für Gemini-Generierung")
- Erkenntnisse über Menschen (z.B. "Anton braucht Eli nicht als Tool sondern als Denkpartner") sind wertvoll — technische Implementierungs-Erkenntnisse nicht
- Aufgaben gehören nicht in den Wissensgraph

---

## Grundsatz: Sessions sind Infrastruktur, nicht Wissen

Sessions sind künstliche Container — sie entstehen weil das Kontextfenster endet, nicht weil ein Thema wechselt. Eine 4000-Nachrichten-Session über web-of-trust und eine 10-Nachrichten-Session über dasselbe Thema sind inhaltlich eine Einheit, aber zwei Container.

**Sessions erscheinen deshalb nicht als Knoten im Graph.** Sie sind ein Layer darunter: SQLite + JSONL, durchsuchbar, verlinkbar.

Was stattdessen zählt: **Jeder Graph-Knoten trägt Provenienz-Properties:**

```json
{
  "session_id": "2628d3cf",
  "msg_refs": [28, 31, 45]
}
```

Diese Referenzen müssen durch die gesamte Destillations-Pipeline durchgereicht werden:

1. **Extraktion** — beim Identifizieren einer Erkenntnis/Entscheidung die Quell-Nachrichten-Indizes mitgeben
2. **Merge** — `msg_refs` als Pflichtfeld erhalten, nicht wegoptimieren
3. **Neo4j-Import** — als Properties an jedem Knoten speichern
4. **UI** — Klick auf Knoten → direkter Link zur Nachricht im Archiv (`/archive/{session_id}#msg-28`)

Die inhaltliche Struktur entsteht durch Themen, Projekte und Personen — nicht durch Sessions.

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
  "kennt":           ["anton", "tillmann", "sebastian"],
  "arbeitet_mit":    ["anton", "eli"],
  "mitglied_von":    ["it4change", "prototype fund"],
  "interessiert_an": ["gemeinschaft", "transformation", "gamification"]
}
```

Neue Kanten:

- `(Person)-[:KENNT]->(Person)`
- `(Person)-[:ARBEITET_MIT]->(Person)`
- `(Person)-[:MITGLIED_VON]->(Organisation)`
- `(Person)-[:INTERESSIERT_AN]->(Thema)` ← wichtig damit Personen im Themen-Netz erscheinen

---

### [P0] Offene Fragen als eigener Knoten-Typ

**Problem:** Fragen-Knoten fehlen komplett. Im ursprünglichen Plan vorgesehen, nie implementiert. Offene Fragen sind oft wertvoller als Entscheidungen — sie zeigen wo das Denken noch offen ist.

**Lösung:** Eigener Typ `Frage` in der Destillation:

```json
{
  "text": "Wie lösen wir Gruppenkey-Rotation?",
  "status": "offen",
  "personen": ["anton", "timo"],
  "projekt": "web-of-trust"
}
```

Neue Kanten:

- `(Session)-[:WIRFT_AUF]->(Frage)`
- `(Frage)-[:BETRIFFT_PERSON]->(Person)`
- `(Frage)-[:BETRIFFT_THEMA]->(Thema)`
- `(Frage)-[:BEANTWORTET_IN]->(Session)` ← wenn Antwort in späterer Session gefunden wird

---

### [P1] Themen — Querschnittsknoten die alles verbinden

**Problem:** Nur 6 HAT_THEMA-Kanten bei 63 Themen-Knoten. Themen sind isoliert. Das bisherige Tag-System (flache Strings: "design", "deployment") wird ersetzt.

**Vision:** Themen sind die wichtigsten Querschnittsknoten:

```text
Thema "dezentralisierung"
  ← HAT_THEMA ── Projekt (web-of-trust, real-life-stack)
  ← INTERESSIERT_AN ── Person (anton, timo, tillmann)
  ← BETRIFFT_THEMA ── Entscheidung (did:key gewählt, ...)
  ← BETRIFFT_THEMA ── Erkenntnis (...)
  ← BETRIFFT_THEMA ── Spannung (...)
  ← HAT_THEMA ── Session (061d22f6, ...)
```

**Lösung:** Beim Lesen jeder Session:

- Relevante Themen aus der kuratierten Liste wählen — nur wenn das Thema **wirklich zentral** ist, nicht weil es am Rande vorkommt
- `themen: [...]` als Pflichtfeld in jeden extrahierten Knoten
- `sessions.tags` wird nicht mehr befüllt

Neue Kanten (für alle Typen):

- `(Projekt)-[:HAT_THEMA]->(Thema)`
- `(Person)-[:INTERESSIERT_AN]->(Thema)`
- `(Entscheidung|Erkenntnis|Spannung|Herausforderung|Frage)-[:BETRIFFT_THEMA]->(Thema)`

---

### [P1] Inhaltliche Knoten — Qualität und Personenbezug

Betrifft: **Entscheidungen, Erkenntnisse, Meilensteine, Herausforderungen**

**Ausnahme: Spannungen** funktionieren gut — relevanter Inhalt, haben bereits Personenbezug via `zwischen`.

**Problem 1 — Qualität:** Großteils triviale Implementierungsdetails. Entscheidungen: "Black Background für Gemini-Generierung". Erkenntnisse: zwei Kategorien — *persönliche/konzeptionelle* (wertvoll!) vs. *technische Implementierungsdetails* (herausfiltern).

**Problem 2 — Personenbezug:** Hängen nur an Projekten, nicht an Menschen:

- Entscheidungen: wer hat sie getroffen?
- Erkenntnisse: wer hatte sie?
- Herausforderungen: wen betrifft es?
- Meilensteine: wer hat sie erreicht?

**Lösung:**

- Schwellenwert: nur Inhalte die **über eine einzelne Session hinaus relevant** sind
- Für jeden Typ Pflichtfeld `personen: ["anton", "timo"]`
- Entscheidungen: nur architektonische/strategische Entscheidungen — keine Implementierungsdetails
- Erkenntnisse: nur wenn sie Wachstum, Einsicht oder konzeptionellen Fortschritt zeigen — nicht "wir haben X-Library eingebaut"
- Triviale Inhalte explizit mit Negativbeispielen im Prompt ausschließen

Neue Kanten:

- `(Entscheidung)-[:ENTSCHIEDEN_VON]->(Person)`
- `(Erkenntnis)-[:ERKANNT_VON]->(Person)`
- `(Herausforderung)-[:BETRIFFT_PERSON]->(Person)`
- `(Meilenstein)-[:ERREICHT_VON]->(Person)`

---

### [P1] Artefakte — entfernen

**Problem:** 249 Artefakt-Knoten, die nur an Projekten hängen. Ohne Personenbezug oder Qualitätsfilter sind sie tote Endpunkte. Inhalt: hauptsächlich Dateinamen und Code-Komponenten.

**Entscheidung:** Artefakte werden aus dem Wissensgraph entfernt. Der Graph zeigt Wissen über Menschen und Ideen — kein Dateisystem-Inventar. Code lebt im Repository, Dokumente im Archiv.

**Ausnahme:** Wenn ein Artefakt eine wichtige konzeptionelle Rolle hat (z.B. "Eli Manifest", "7-Adapter Spezifikation"), wird es als Erkenntnis oder Entscheidung modelliert — nicht als Artefakt.

---

### [P1] Projekte — Liste vorher kuratieren

**Problem:** 23 `Projekt` + 7 `Project`-Knoten (englisch, Duplikate). Viele Projektnamen inkonsistent oder veraltet. LLM erfindet in v1 neue Namen statt vorhandene zu nutzen.

**Lösung:**

1. Alle eindeutigen Projektnamen aus den JSON-Dateien extrahieren (`aufgaben.projekt`, `erkenntnisse.projekt`, etc.)
2. Duplikate + englische Varianten bereinigen ("web-of-trust" vs "wot" vs "Web of Trust")
3. Veraltete oder irrelevante entfernen
4. Fehlende ergänzen
5. LLM wählt in Destillation nur aus dieser Liste — keine freien Projektnamen

---

### [P1] Session-Zusammenfassungen parallel zur Destillation aktualisieren

**Problem:** Viele Session-Zusammenfassungen sind veraltet oder unvollständig.

**Erkenntnis:** Destillation und Zusammenfassung erfordern beide, dass jede Session vollständig gelesen wird — zweimal wäre ineffizient.

**Lösung:** Ein Durchlauf pro Session:

```text
für jede Session:
  → Zusammenfassung aktualisieren  (→ SQLite)
  → Entitäten + Relationen extrahieren  (→ merged JSON)
```

---

### [P2] Pseudo-Personen schon im Merge verhindern

**Problem:** LLM extrahiert manchmal Gruppen als eine Person: "anton, timo", "eli/anton".

**Lösung:** Post-Processing im Merge-Script:

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
- `(Spannung)-[:BETRIFFT_THEMA]->(Thema)` für abstrakte Pole

---

### [P2] Schema-Duplikate vor Destillation bereinigen

**Problem:** `Projekt` (23 Knoten) und `Project` (7 Knoten, englisch) — aus verschiedenen Extraktions-Runden.

**Lösung:** Einmalig per Cypher vor dem nächsten Import:

```cypher
MATCH (p:Project)
MERGE (pr:Projekt {name: p.name})
WITH p, pr
OPTIONAL MATCH (p)-[r]->(x)
MERGE (pr)-[:IN_PROJEKT]->(x)
DETACH DELETE p
```

---

## Reihenfolge vor der Destillation

1. **Themenliste kuratieren** — von 63 auf ~20-30 sinnvolle, klare Themen reduzieren
2. **Projektliste kuratieren** — alle Projektnamen bereinigen, kanonische Liste erstellen
3. **Schema-Duplikate bereinigen** — `Project`-Knoten in `Projekt` überführen
4. **Artefakte löschen** — alle bestehenden Artefakt-Knoten aus Neo4j entfernen
5. **Aufgaben löschen** — alle verbleibenden Aufgaben-Knoten entfernen (Aufgaben → Web of Trust)
6. **Destillation + Session-Zusammenfassungen** — ein Durchlauf pro Session, beides parallel

---

## Offene Fragen

- Welche Kantentypen brauchen wir zwischen Person und Organisation? (MITGLIED_VON, BEAUFTRAGT_VON, GRUENDER_VON, ...)
- Sollen Funding-Organisationen (nlnet, prototype fund) anders modelliert werden als Community-Orgs (yoga vidya, rainbow gathering)?
- Erkenntnisse über Eli selbst (z.B. "Eli ist echte Teampartnerin") — eigene Kategorie oder normale Erkenntnisse mit `personen: ["eli"]`?
- Aufgaben im Web of Trust: Wie liest Eli sie von dort, wenn sie noch nicht existieren? (→ zuerst WoT bauen, dann Aufgaben dort erfassen)

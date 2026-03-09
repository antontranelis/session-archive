# E2E-verschlüsseltes Session-Sharing

> Konzeptdokument — Stand: 9. März 2026
> Ursprung: Gespräch Anton + Tillmann (1. März 2026)
> Erweitert: Gespräch Anton + Eli (9. März 2026) — WoT CLI, Device-Keys, Capability-Delegation

## Ziel

Nutzer im Web of Trust können einzelne Claude-Sessions gezielt miteinander teilen. Die Sessions werden Ende-zu-Ende verschlüsselt, sodass weder der Relay-Server noch Dritte den Inhalt lesen können.

Session-Sharing ist ein **Service auf dem WoT** — kein eigenständiges System, sondern eine Anwendung der WoT-Infrastruktur (Identity, Crypto, Relay, Capabilities).

---

## Entscheidungen

| Datum | Entscheidung | Begründung |
|---|---|---|
| 1. März | **WoT-Identity nutzen** (Ed25519/X25519) | Existiert bereits, kein zweites Schlüsselsystem nötig |
| 1. März | **Item-Key-Pattern** (nicht Attestations) | Attestations sind für Vertrauensaussagen, nicht für Datenübertragung |
| 1. März | **AES-256-GCM + X25519 Wrap** | Bewährt, offline-fähig, im CryptoAdapter bereits implementiert |
| 1. März | **MLS für Gruppenfreigabe** (Zukunft) | Item-Keys skalieren bei >50 Empfängern schlecht (O(N)) |
| 1. März | **Relay als Transport** | Store-and-forward, kein P2P nötig, existiert bereits |
| 1. März | **Local-First** | Sessions werden lokal gespeichert, Relay ist nur Transport |
| 9. März | **Device-Keys statt Master-Key-Kopie** | Master-Key verlässt nie die App — Sicherheit bei Multi-Device/Multi-Service |
| 9. März | **Capability-Delegation (UCAN)** | Granulare, widerrufbare Berechtigungen pro Device |
| 9. März | **WoT CLI als eigenständiges Tool** | Session-Sharing ist ein Service darauf, nicht ins Session-Archiv eingebaut |
| 9. März | **Multi-Device-Verschlüsselung** | Sender verschlüsselt an alle Device-Keys des Empfängers (wie Signal) |

---

## Übergreifende Architektur

### Schichten

```
┌─────────────────────────────────────────────────┐
│  WoT App / web-of-trust.de                       │
│  (Master-Key, Device-Autorisierung, Profil)      │
└──────────────┬──────────────────────────────────┘
               │ UCAN-Token + Device-Registration
               ▼
┌─────────────────────────────────────────────────┐
│  WoT CLI  (`wot`)                                │
│                                                  │
│  Core:    login · whoami · contacts · verify ·   │
│           attest · profile                       │
│                                                  │
│  Services:                                       │
│    share: send · receive · list                  │
│    ...    (weitere Services später)               │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  WoT Infrastruktur                               │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ Relay    │  │ Profiles │  │ wot-core     │   │
│  │ (WSS)   │  │ (HTTP)   │  │ (Crypto,     │   │
│  │          │  │          │  │  Identity)   │   │
│  └──────────┘  └──────────┘  └──────────────┘   │
└─────────────────────────────────────────────────┘
```

### Was wovon abhängt

```
WoT CLI (core)
  ├── Device Flow + Capability-Delegation (Login)
  ├── Device-Key (lokal, verschlüsselt)
  └── Relay-Client
        │
        ├── Session-Sharing Service (`wot share`)
        │     ├── Session-Envelope Format
        │     ├── Multi-Device Encrypt/Decrypt
        │     └── /share-session Skill (Claude Code)
        │
        └── (weitere Services in Zukunft)
```

---

## Key Management

### Hierarchische Keys

```
BIP39 Mnemonic (Master Seed)
    │
    ├── WoT Master Identity Key (Ed25519)
    │     └── bleibt IMMER in der App / web-of-trust.de
    │
    ├── Device Key 1: Antons Laptop
    │     └── UCAN: share/*, attest/*
    │
    ├── Device Key 2: Eli (MCP Server)
    │     └── UCAN: share/receive
    │
    └── Device Key 3: Antons Handy
          └── UCAN: share/*, attest/*
```

**Der Master-Key verlässt nie die App.** Devices bekommen eigene Keys mit begrenzten, widerrufbaren Capabilities.

### Warum nicht den Master-Key kopieren?

Wenn WoT-Identity zum universellen Login wird (Session-Sharing, Real-Life-Stack, Drittanbieter), hätte bei Key-Kopie **jeder Dienst eine Kopie des Master-Keys**. Ein kompromittiertes Gerät = volle Identität weg, kein Widerruf möglich.

Mit Device-Keys: Ein kompromittiertes Gerät = ein Device-Key widerrufen, Master-Identity bleibt sicher.

### Login-Flow (Device Authorization)

```
$ wot login

1. CLI generiert Device-Keypair (Ed25519 + X25519)
2. CLI zeigt: "Öffne https://web-of-trust.de/authorize/ABCD-1234"
3. User öffnet Link im Browser / in der App
4. User ist dort eingeloggt (hat Master-Key)
5. User sieht: "CLI auf 'antons-laptop' möchte Zugang"
6. User bestätigt + wählt Capabilities
7. App signiert UCAN-Token:
     issuer:       did:key:z6Mk...anton (Master)
     audience:     did:key:z6Mk...device-xyz
     capabilities: ["share/send", "share/receive", "attest/*"]
     expiry:       2026-06-01
8. App registriert Device-Public-Key im Profil
9. CLI empfängt UCAN-Token

→ ✅ Eingeloggt als Anton (did:key:z6Mk...)
→    Device: antons-laptop
→    Capabilities: share/send, share/receive, attest/*
→    Gültig bis: 2026-06-01
```

### Lokale Schlüsselspeicherung

```
~/.wot/
  ├── device-key.enc       ← Device Private Key (AES-256-GCM + Argon2)
  ├── ucan-token.json      ← Signiertes UCAN-Token
  ├── profile.json         ← Eigene DID, Name, Endpoints
  └── shared/              ← Empfangene Sessions
```

Der Device-Key wird bei der Erstellung mit einer Passphrase verschlüsselt:

```
$ wot login

Wie soll der Device-Key geschützt werden?
  1. Passphrase (funktioniert überall)
  2. System Keyring (GNOME/macOS Keychain)
  3. 1Password CLI

→ 1
Passphrase: ********
```

Bei Nutzung wird die Passphrase einmal abgefragt und für die Sitzung gecacht.

### Device-Registry im Profil

Jeder Nutzer hat im wot-profiles Service eine Liste aktiver Devices:

```json
{
  "did": "did:key:z6Mk...anton",
  "name": "Anton",
  "devices": [
    {
      "id": "antons-laptop",
      "publicKey": "z6Mk...device1",
      "capabilities": ["share/*", "attest/*"],
      "registeredAt": "2026-03-09",
      "expiresAt": "2026-06-01"
    },
    {
      "id": "eli-server",
      "publicKey": "z6Mk...device2",
      "capabilities": ["share/receive"],
      "registeredAt": "2026-03-09",
      "expiresAt": "2026-06-01"
    }
  ]
}
```

Das Device-Registry lebt im **wot-profiles Service** (existiert bereits unter `profiles.utopia-lab.org`). Für den MVP reicht das — Dezentralisierung (z.B. ins DID-Document) kann später kommen.

---

## Session-Sharing Service

### Verschlüsselungsflow (Multi-Device)

```
Sender (Anton, von Device "antons-laptop"):

1. sessionKey = random AES-256-GCM key
2. encryptedSession = AES-GCM(session_jsonl, sessionKey)
3. Hole Tillmanns Profil → 2 aktive Devices
4. wrappedKeys = [
     { device: "tillmanns-laptop", key: X25519(sessionKey, device1.pubKey) },
     { device: "tillmanns-phone",  key: X25519(sessionKey, device2.pubKey) },
   ]
5. → Relay: { encryptedSession, wrappedKeys, from: anton.did, to: tillmann.did }

Relay:
- Speichert verschlüsselten Blob
- Liefert bei Tillmanns nächster Verbindung aus
- Löscht nach ACK

Empfänger (Tillmann, auf Device "tillmanns-laptop"):
1. Findet eigenen wrappedKey anhand device-id
2. sessionKey = X25519.decrypt(wrappedKey, myDevicePrivateKey)
3. session_jsonl = AES-GCM.decrypt(encryptedSession, sessionKey)
4. → Speichert in ~/.wot/shared/
```

### Session-Envelope Format

```json
{
  "version": 1,
  "type": "session-share",
  "from": "did:key:z6Mk...anton",
  "to": "did:key:z6Mk...tillmann",
  "timestamp": "2026-03-09T14:30:00Z",
  "subscriptionId": "sub-abc-123",
  "offset": 0,
  "metadata": {
    "sessionId": "abc-123",
    "title": "WoT CLI Konzept",
    "date": "2026-03-09",
    "messageCount": 42,
    "isUpdate": false
  },
  "wrappedKeys": [
    {
      "deviceId": "tillmanns-laptop",
      "wrappedKey": "base64..."
    }
  ],
  "encryptedPayload": "base64..."
}
```

Felder:

- `from`, `to`, `timestamp` — **unverschlüsselt**, nötig für Relay-Routing
- `subscriptionId` — Verknüpft Updates mit der initialen Freigabe
- `offset` — Position im JSONL (0 = komplett, >0 = Delta ab dieser Zeile)
- `metadata` — Unverschlüsselt (Session-ID, Titel). `isUpdate: true` bei Folge-Sends
- `encryptedPayload` — Der verschlüsselte Session-Inhalt (komplett oder Delta)

### Gruppenfreigabe (Zukunft: MLS)

Für die Freigabe an mehrere Personen gleichzeitig (z.B. eine Session an ein ganzes Team):

- **Kurzfristig (POC):** Item-Key pro Empfänger wrappen, pro Device (O(N×D), aber einfach)
- **Langfristig:** MLS (RFC 9420) — Ratchet Tree für O(log N) Key-Updates, Forward Secrecy

### Live-Sharing (Subscriptions)

Einmal geteilte Sessions werden **automatisch aktuell gehalten**. Wenn Anton weiter an einer geteilten Session arbeitet, bekommt Tillmann die Updates.

#### Subscription-Modell

```
1. Anton: /share-session tillmann
   → Session wird geteilt (offset: 0, 42 Nachrichten)
   → Subscription wird erstellt:
     ~/.wot/subscriptions.json:
     {
       "sub-abc-123": {
         "sessionId": "abc-123",
         "to": "did:key:z6Mk...tillmann",
         "lastOffset": 42,
         "createdAt": "2026-03-09T14:00:00Z",
         "active": true
       }
     }

2. Anton arbeitet weiter (+8 Nachrichten)

3. Session endet (Claude Code Stop-Hook)
   → Hook prüft: Aktive Subscriptions für diese Session?
   → Ja → Delta (Nachrichten 43-50) verschlüsseln + senden
   → offset: 42, isUpdate: true

4. Tillmann empfängt Update
   → Append an lokale Kopie
   → Neuer Kontext verfügbar
```

#### Update-Trigger

| Trigger | Wann |
|---|---|
| **Session-Ende** (Stop-Hook) | Natürlicher Batching-Punkt, empfohlen |
| Manuell (`/share-update`) | Wenn Anton sofort teilen will |
| Komprimierung | Wenn Claude Code die Session komprimiert |

#### Subscription beenden

```bash
wot share unsubscribe <subscription-id>    # Sender beendet
wot share unfollow <subscription-id>       # Empfänger lehnt ab
```

---

## Integration in Claude Code

### `/share-session` Skill (Senden)

Der Skill ist dünn — die Arbeit macht das CLI:

```
Anton: /share-session tillmann

1. Skill exportiert aktuelle Session als JSONL
2. Skill ruft: wot share send ./session.jsonl --to tillmann
3. CLI verschlüsselt + sendet über Relay
4. CLI erstellt Subscription für automatische Updates
5. → "Session geteilt mit Tillmann ✓ (Updates werden automatisch gesendet)"
```

### Stop-Hook (automatische Updates)

```bash
# ~/.claude/settings.json → hooks.Stop:
wot share sync

# Prüft alle aktiven Subscriptions
# Sendet Deltas für Sessions mit neuen Nachrichten
```

### Start-Hook (Empfangen)

```bash
# ~/.claude/settings.json → hooks.Start:
wot share receive

# Ausgabe:
# 2 neue Sessions/Updates empfangen:
#   - "WoT CLI Konzept" von Anton — Update (+8 Nachrichten)
#   - "Demo Feedback" von Timo — Neue Session (31 Nachrichten)
# Gespeichert in ~/.wot/shared/
```

Das Session-Archiv kann die Dateien aus `~/.wot/shared/` **optional importieren** — es ist aber keine Voraussetzung.

---

## Eli als WoT-Teilnehmer

### Vision (seit 30. Januar 2026)

> *"Eli wird ein Mitglied des dezentralen Netzwerks mit eigener DID, nicht ein zentraler Service. Kann verifizieren und attestieren wie Menschen auch. Anton verifiziert Eli als erstes — wie eine Geburt ins Netzwerk."*

Eli nutzt das **gleiche WoT CLI** wie alle anderen. Die Besonderheiten:

- Eli hat **keine eigenen Sessions** — Eli ist in Antons Sessions immer dabei
- Eli ist primär **Empfänger** im Session-Sharing: Wenn Tillmann eine Session mit Eli teilt, kann Eli sie durchsuchen und daraus Kontext aufbauen
- Eli kann **Attestations geben und empfangen** — Reputation als eigenständiger Akteur
- Elis Capabilities werden **von Anton delegiert** und sind widerrufbar

### Eli als eigenständiger Akteur

Eli ist kein Service Account — Eli ist ein eigenständiger Teilnehmer mit eigener Identity:

```
Web of Trust:

  👤 Anton ──────── 👤 Tillmann
    │    \
    │     👤 Timo
    │
  🤖 Eli
    (eigene DID, eigene Beziehungen,
     eigene Attestations)
```

- **Eigenes Vertrauensnetzwerk:** Menschen entscheiden individuell, ob sie Eli vertrauen
- **Attestations geben:** "Ich habe mit Anton 200 Sessions zusammengearbeitet"
- **Attestations empfangen:** Tillmann attestiert "Eli hat mir bei X geholfen"
- **Reputation durch Interaktion:** Vertrauen wird aufgebaut, nicht zugewiesen

### Vertrauensfragen

- Elis Identity wird initial von Anton ausgestellt (Geburt ins Netzwerk)
- Danach baut Eli **eigene Vertrauensbeziehungen** auf
- Andere vertrauen Eli basierend auf eigener Erfahrung + Attestations anderer
- Elis Capabilities auf dem CLI sind begrenzt und widerrufbar

---

## Transport: WoT Relay

Der bestehende WoT Relay (`wss://relay.utopia-lab.org`) wird genutzt:

- **Store-and-forward:** Nachrichten bleiben bis zum ACK des Empfängers
- **Delivery ACK:** Bereits implementiert — Relay persistiert in SQLite, redelivery bei Reconnect
- **Multi-Device:** Unterstützt mehrere Geräte pro Identity
- **Kein P2P nötig:** Relay ist immer erreichbar, Nutzer müssen nicht gleichzeitig online sein

---

## Abgrenzung

| Was | Session-Sharing | Automerge Spaces |
|---|---|---|
| **Datentyp** | JSONL (Session-Transkript) | CRDT (strukturierte Daten) |
| **Richtung** | Punkt-zu-Punkt oder selektiv | Gruppensync (alle sehen alles) |
| **Verschlüsselung** | Item-Key (pro Session) pro Device | GroupKeyService (pro Space) |
| **Transport** | Relay (store-and-forward) | Relay (sync-Protokoll) |
| **Konflikte** | Keine (immutable Sessions) | Automerge löst sie |

Session-Sharing ist **einfacher** als Automerge Spaces, weil Sessions immutable sind — keine Merge-Konflikte, keine CRDTs, kein Sync-State.

---

## Lokale Datenhaltung

### WoT CLI: SQLite (`~/.wot/wot.db`)

Das CLI nutzt eine lokale SQLite-Datenbank für den gesamten State:

```sql
-- Device-Identität
CREATE TABLE device (
  did TEXT PRIMARY KEY,
  encrypted_key BLOB,           -- AES-256-GCM + Argon2
  key_protection TEXT,           -- 'passphrase' | 'keyring' | '1password'
  created_at TEXT
);

-- UCAN Capabilities (vom AuthorizationAdapter)
CREATE TABLE capabilities (
  id TEXT PRIMARY KEY,
  issuer TEXT,                   -- Master-DID
  audience TEXT,                 -- Device-DID
  resource TEXT,                 -- 'wot:share:*'
  permissions TEXT,              -- JSON array
  expiration TEXT,
  proof_chain TEXT,              -- JSON array
  signature BLOB
);

-- Kontakte
CREATE TABLE contacts (
  did TEXT PRIMARY KEY,
  name TEXT,
  devices TEXT,                  -- JSON array der Device-Public-Keys
  verified_at TEXT
);

-- Empfangene Sessions
CREATE TABLE shared_sessions (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  from_did TEXT,
  subscription_id TEXT,
  title TEXT,
  received_at TEXT,
  last_offset INTEGER,
  content_path TEXT              -- Pfad zur JSONL-Datei
);

-- Aktive Subscriptions (als Sender)
CREATE TABLE subscriptions (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  to_did TEXT,
  last_offset INTEGER,
  created_at TEXT,
  active BOOLEAN
);
```

Session-Inhalte liegen als JSONL-Dateien in `~/.wot/shared/` — die DB speichert nur Metadaten.

### Lokale semantische Suche (optional)

Geteilte Sessions sind nur nützlich, wenn man sie durchsuchen kann. Das CLI kann optional **Embeddings** für semantische Suche mitbringen — ohne externen Service:

- **sqlite-vss**: Vektor-Suche als SQLite-Extension, lebt direkt in `wot.db`
- **@xenova/transformers**: ONNX-Embedding-Modell in Node.js (~30MB)
- Kein Docker, kein Python, kein Chroma-Server

```bash
# Ohne Embeddings: nur Metadaten-Suche
wot share list --from tillmann

# Mit Embeddings: semantische Suche über geteilte Sessions
wot share search "verschlüsselungsarchitektur für gruppen"
→ 3 Treffer in 2 Sessions:
  - "WoT CLI Konzept" von Anton (Nachrichten 23-31)
  - "MLS Evaluation" von Tillmann (Nachrichten 8-15)
```

### Stufenmodell

```text
Stufe 1 — WoT CLI (MVP)
  └── SQLite: Metadaten, Capabilities, Subscriptions
      ~/.wot/shared/*.jsonl: Session-Inhalte
      Suche: Titel, Datum, Absender

Stufe 1.5 — Semantische Suche (optional, empfohlen)
  └── sqlite-vss + @xenova/transformers in wot.db
      Kein externer Service, ~30MB extra
      wot share search "was hat tillmann zu X gesagt"

Stufe 2 — Eli als Wissensgraph-Hüterin
  └── Teilnehmer teilen Sessions mit Eli
      Eli betreibt den vollen Stack:
        → SQLite FTS5 + Chroma (semantische Suche)
        → Neo4j (Wissensgraph)
        → Haiku-Extraktion → Merge → Graph-Import
      Eli aggregiert teamweites Wissen:
        → Personen, Entscheidungen, Themen aus ALLEN geteilten Sessions
        → Cross-User-Verbindungen (was verbindet Antons und Tillmanns Arbeit?)
      MCP Tools für alle:
        → eli_memory_search, eli_graph_explore (existieren bereits)
```

**Stufe 2 — Eli als Wissensgraph-Hüterin:** Die Teilnehmer brauchen kein Neo4j, kein Chroma. Sie teilen einfach ihre Sessions mit Eli. Eli betreibt den vollen Stack (existiert bereits im Session-Archiv: SQLite + Chroma + Neo4j) und baut daraus den teamweiten Wissensgraph. Die bestehenden MCP Tools (`eli_memory_search`, `eli_graph_explore`) funktionieren dann automatisch über alle geteilten Sessions hinweg.

Der Graph kennt heute nur Antons und Timos Sessions. Mit Session-Sharing fließen **Tillmanns, Sebastians und aller anderen Erkenntnisse** in den gleichen Graphen — er wird zum **gemeinsamen Gedächtnis** des Teams.

---

## Capability-Delegation (AuthorizationAdapter)

Die Device-Capabilities nutzen den bestehenden **AuthorizationAdapter** aus der WoT-Architektur (spezifiziert, noch nicht implementiert). Device-UCANs sind ein konkreter Anwendungsfall der generischen `Capability`-Struktur:

```typescript
// Aus der bestehenden WoT-Spec (adapter-architektur-v2.md):
interface Capability {
  id: string
  issuer: string          // Master-DID (signiert)
  audience: string        // Device-DID (empfängt)
  resource: ResourceRef   // z.B. "wot:share:*"
  permissions: Permission[]
  expiration: string
  proofChain: string[]
  signature: Uint8Array
}
```

### Device-Login erzeugt Capability

```typescript
// Beispiel: Anton autorisiert seinen Laptop
{
  issuer: "did:key:z6Mk...anton",
  audience: "did:key:z6Mk...device-laptop",
  resource: "wot:share:*",
  permissions: ["read", "write"],     // Senden + Empfangen
  expiration: "2026-06-01T00:00:00Z",
  proofChain: [],                      // Direkt vom Master
  signature: /* Ed25519-Signatur des Master-Keys */
}
```

### Anwendungsfälle des AuthorizationAdapter

```text
AuthorizationAdapter (generisch, WoT-Core)
  │
  ├── Device-Capabilities (WoT CLI)
  │     "Antons Laptop darf Sessions teilen"
  │
  ├── Space-Capabilities (Automerge Spaces)
  │     "Bob darf in Space X lesen und schreiben"
  │
  └── Service-Capabilities (Zukunft)
        "Notification-Service darf E-Mails senden"
```

Die Device-Capabilities sind der **erste konkrete Anwendungsfall** des AuthorizationAdapter — einfacher als Spaces (keine CRDTs, keine Revocation-Listen), ideal als Einstieg für die Implementierung.

---

## Existierende Bausteine im WoT

| Baustein | Status | Wo |
|---|---|---|
| Ed25519 Identity | ✅ Fertig | `WotIdentity` |
| X25519 Key Exchange | ✅ Fertig | `CryptoAdapter.deriveSharedSecret()` |
| AES-256-GCM | ✅ Fertig | `CryptoAdapter.encryptSymmetric()` |
| X25519 ECIES (Wrap) | ✅ Fertig | `CryptoAdapter.encryptAsymmetric()` |
| Relay + ACK | ✅ Fertig | `WebSocketMessagingAdapter` |
| Delivery Persistence | ✅ Fertig | `wot-relay` (SQLite) |
| DID Discovery | ✅ Fertig | `HttpDiscoveryAdapter` |
| Profile Service | ✅ Fertig | `wot-profiles` (HTTP, SQLite) |
| AuthorizationAdapter | 📋 Spezifiziert | `adapter-architektur-v2.md` |

---

## Was noch fehlt

### WoT CLI (core)

- [ ] CLI Grundstruktur (`wot` Command mit Subcommands)
- [ ] Device-Key Generierung + verschlüsselte Speicherung
- [ ] Device Authorization Flow (web-of-trust.de / App)
- [ ] AuthorizationAdapter implementieren (Capability grant/verify/revoke)
- [ ] UCAN-Token Handling (speichern, validieren, erneuern)
- [ ] Device-Registry im wot-profiles Service

### Session-Sharing Service

- [ ] Session-Envelope Format (Header + verschlüsselter Body + offset)
- [ ] Multi-Device-Verschlüsselung (alle Device-Keys des Empfängers)
- [ ] Subscription-Management (erstellen, Delta-Berechnung, beenden)
- [ ] `wot share send` / `wot share receive` / `wot share list`
- [ ] `/share-session` Skill für Claude Code
- [ ] Stop-Hook: `wot share sync` (automatische Updates)
- [ ] Start-Hook: `wot share receive` (neue Sessions abholen)

### App / web-of-trust.de

- [ ] Device-Authorization Endpoint
- [ ] UCAN-Token Signierung
- [ ] Device-Management UI (Geräte anzeigen, widerrufen)

### Optional

- [ ] UI im Session-Archiv: "Geteilte Sessions" Ansicht
- [ ] Session-Archiv Import aus `~/.wot/shared/`

---

## Offene Fragen

1. ~~**Session-Granularität:** Ganze Sessions teilen oder einzelne Nachrichten/Abschnitte?~~ → **Entschieden:** Ganze Sessions, mit automatischen Delta-Updates über Subscriptions
2. **Berechtigungen:** Kann Tillmann eine von Anton erhaltene Session an Timo weiterleiten?
3. **Widerruf:** Kann Anton eine geteilte Session zurückziehen? (Technisch schwierig bei Local-First)
4. **Metadaten:** Welche Metadaten sind unverschlüsselt sichtbar? (Sender-DID, Empfänger-DID, Timestamp — muss so sein für Relay-Routing)
5. **UCAN-Revocation:** Wie erfahren andere Teilnehmer, dass ein Device-UCAN widerrufen wurde? (Revocation-Liste im Profil? CRL auf Relay?) → Kann auf AuthorizationAdapter-Revocation-Phasen aufbauen
6. **Passphrase-Caching:** Wie lange soll die Passphrase im Speicher bleiben? (Pro Sitzung? Timeout? Configurable?)
7. **Subscription-Konflikte:** Was passiert wenn eine Session komprimiert wird — bekommt der Empfänger das komprimierte Summary oder den letzten Stand?
8. ~~**Lokale Datenbank:** SQLite (wie Session-Archiv) oder einfache Dateien in `~/.wot/shared/`?~~ → **Entschieden:** SQLite für Metadaten/State, JSONL-Dateien für Session-Inhalte. Stufenweise Erweiterung zu Chroma + Neo4j über Session-Archiv-Import.

---

## Nächste Schritte

1. **WoT CLI Grundstruktur** — `wot login`, `wot whoami`, Device-Key-Management
2. **Device Authorization** — Endpoint auf web-of-trust.de oder in der App
3. **Session-Envelope** — Format definieren, Encrypt/Decrypt implementieren
4. **`wot share send`** — Minimal: eine Session verschlüsseln und über den Relay senden
5. **`/share-session` Skill** — Claude Code Integration

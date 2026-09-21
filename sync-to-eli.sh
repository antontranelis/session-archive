#!/bin/bash
# Sync Claude Code and Codex sessions to Eli's server
# Claude sessions are synced like before; Codex sessions keep their date tree.
# Usage: ./sync-to-eli.sh [username]
#
# Zugang: zwei eigene Schluessel, kein Hardwareschluessel.
#
# Bis zum 21.09.2026 lief das hier ueber Antons Nitrokey. Alle 15 Minuten,
# 116 Projektordner, je ein eigenes ssh - und jedes wollte eine Beruehrung.
# 175 Anfragen in sieben Tagen, keine beantwortet, kein Byte uebertragen.
# Ein Timer hat keinen Finger; die Einrichtung konnte gar nicht
# funktionieren. Aufgefallen ist es erst, als Elis Server wiederhergestellt
# war - vorher scheiterte das ssh schon an der Verbindung.
#
# Jetzt je Ziel ein Schluessel, auf der Gegenseite eingesperrt in einen
# festen Befehl (rrsync, nur schreiben, nur in dieses eine Verzeichnis,
# siehe real-life-org/infrastructure, hosts/eli/default.nix).
#
# Zwei Schluessel statt einem, weil ein Schluessel mit Wurzel
# /home/eli/geist/archive auch in Timos Verzeichnis schreiben duerfte.
#
# Die Ziele sind deshalb LEER: rrsync setzt sein Wurzelverzeichnis selbst
# davor. Ein absoluter Pfad wird abgewiesen.

set -u

USER="${1:-anton}"
CLAUDE_DIR="$HOME/.claude/projects"
CODEX_DIR="$HOME/.codex/sessions"
HOST="eli@82.165.138.182"

SCHLUESSEL_ANTON="$HOME/.ssh/id_ed25519_eli_sync"
SCHLUESSEL_CODEX="$HOME/.ssh/id_ed25519_eli_codex"

# Eine Verbindung fuer alle Projektordner statt einer je Ordner. Der Kanal
# bleibt eine Minute offen; danach raeumt ihn ssh selbst ab.
#
# JE SCHLUESSEL EIN EIGENER KANAL. %r@%h loest sich fuer beide Schluessel
# zu eli@82.165.138.182 auf - ein gemeinsamer Pfad bedeutet also: der
# Codex-Aufruf uebernimmt die schon offene Verbindung des ersten
# Schluessels, samt dessen erzwungenem Befehl. Die Codex-Sitzungen
# landeten dann in archive/anton statt archive/codex, rsync meldete
# Erfolg, und die Dateien laegen still am falschen Ort.
#
# OHNE DIE PERSOENLICHE SSH-KONFIGURATION. IdentitiesOnly=yes beschraenkt
# auf die *angegebenen* Identitaeten - und dazu zaehlt auch das
# IdentityFile aus ~/.ssh/config fuer diesen Host. Dort steht der
# Nitrokey. Er wurde zuerst angeboten, der Server nahm ihn, und der hat
# keinen erzwungenen Befehl: kein rrsync, "eli@host:" hiess schlicht Elis
# Heimatverzeichnis. 384 Sitzungsdateien lagen am 21.09.2026 in
# /home/eli/ statt im Archiv, rsync meldete Erfolg.
#
# -F /dev/null laesst die Konfiguration aus. Dann muss known_hosts
# ausdruecklich genannt werden, sonst prueft ssh den Host gegen nichts.
ssh_mit() {
    # $1 = Name des Kanals, $2 = Schluesseldatei
    echo "ssh -F /dev/null -i $2 -o IdentitiesOnly=yes" \
         "-o UserKnownHostsFile=$HOME/.ssh/known_hosts" \
         "-o ControlMaster=auto -o ControlPath=/tmp/eli-sync-$1-%r@%h -o ControlPersist=60"
}

if [ ! -d "$CLAUDE_DIR" ]; then
    echo "Error: $CLAUDE_DIR not found"
    exit 1
fi

if [ ! -f "$SCHLUESSEL_ANTON" ]; then
    echo "Error: $SCHLUESSEL_ANTON fehlt - ohne eigenen Schluessel laeuft der Sync nicht"
    exit 1
fi

COUNT=$(find "$CLAUDE_DIR" -name '*.jsonl' 2>/dev/null | wc -l)
DIRS=$(ls -d "$CLAUDE_DIR"/*/ 2>/dev/null | wc -l)

echo "Syncing $COUNT sessions from $DIRS projects for $USER..."

# Fehler zaehlen statt verschlucken: ein Sync, der nichts uebertraegt und
# trotzdem "Done" meldet, ist genau das, was hier monatelang passiert ist.
FEHLER=0
for dir in "$CLAUDE_DIR"/*/; do
    LOCAL_COUNT=$(ls "$dir"*.jsonl 2>/dev/null | wc -l)
    [ "$LOCAL_COUNT" -eq 0 ] && continue

    rsync -az \
        -e "$(ssh_mit anton "$SCHLUESSEL_ANTON")" \
        --include='*.jsonl' \
        --exclude='*' \
        "$dir" "$HOST:" || FEHLER=$((FEHLER + 1))
done

if [ "$FEHLER" -gt 0 ]; then
    echo "FEHLER: $FEHLER von $DIRS Projektordnern nicht uebertragen"
else
    echo "Done. $COUNT sessions synced."
fi
CLAUDE_FEHLER=$FEHLER

if [ -d "$CODEX_DIR" ]; then
    CODEX_COUNT=$(find "$CODEX_DIR" -name '*.jsonl' 2>/dev/null | wc -l)

    if [ "$CODEX_COUNT" -gt 0 ]; then
        if [ ! -f "$SCHLUESSEL_CODEX" ]; then
            # Es gibt Codex-Sitzungen, aber keinen Schluessel dafuer. Das
            # ist kein Ueberspringen, das ist ein Fehlschlag.
            echo "FEHLER: $CODEX_COUNT Codex-Sitzungen, aber $SCHLUESSEL_CODEX fehlt"
            FEHLER=$((FEHLER + 1))
        else
            echo "Syncing $CODEX_COUNT Codex sessions..."
            if rsync -az \
                -e "$(ssh_mit codex "$SCHLUESSEL_CODEX")" \
                --include='*/' \
                --include='*.jsonl' \
                --exclude='*' \
                "$CODEX_DIR/" "$HOST:"
            then
                echo "Done. $CODEX_COUNT Codex sessions synced."
            else
                echo "FEHLER: Codex-Sitzungen nicht uebertragen"
                FEHLER=$((FEHLER + 1))
            fi
        fi
    else
        echo "No Codex sessions found in $CODEX_DIR."
    fi
else
    echo "Skipping Codex: $CODEX_DIR not found."
fi

# Der Rueckgabewert deckt BEIDE Teile ab. Ein Lauf, in dem nur Codex
# scheitert, darf nicht als Erfolg enden - sonst meldet systemd gruen,
# waehrend nichts ankommt. Genau so war dieser Sync monatelang unbemerkt
# kaputt.
if [ "$FEHLER" -gt 0 ]; then
    echo "Lauf unvollstaendig: $CLAUDE_FEHLER bei Claude, $((FEHLER - CLAUDE_FEHLER)) bei Codex"
    exit 1
fi

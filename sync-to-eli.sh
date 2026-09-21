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
KANAL="/tmp/eli-sync-%r@%h"
MULTIPLEX="-o ControlMaster=auto -o ControlPath=$KANAL -o ControlPersist=60"

ssh_mit() {
    echo "ssh -i $1 -o IdentitiesOnly=yes $MULTIPLEX"
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
        -e "$(ssh_mit "$SCHLUESSEL_ANTON")" \
        --include='*.jsonl' \
        --exclude='*' \
        "$dir" "$HOST:" || FEHLER=$((FEHLER + 1))
done

if [ "$FEHLER" -gt 0 ]; then
    echo "WARNUNG: $FEHLER von $DIRS Projektordnern nicht uebertragen"
else
    echo "Done. $COUNT sessions synced."
fi

if [ -d "$CODEX_DIR" ]; then
    CODEX_COUNT=$(find "$CODEX_DIR" -name '*.jsonl' 2>/dev/null | wc -l)

    if [ "$CODEX_COUNT" -gt 0 ]; then
        if [ ! -f "$SCHLUESSEL_CODEX" ]; then
            echo "Codex uebersprungen: $SCHLUESSEL_CODEX fehlt"
        else
            echo "Syncing $CODEX_COUNT Codex sessions..."
            if rsync -az \
                -e "$(ssh_mit "$SCHLUESSEL_CODEX")" \
                --include='*/' \
                --include='*.jsonl' \
                --exclude='*' \
                "$CODEX_DIR/" "$HOST:"
            then
                echo "Done. $CODEX_COUNT Codex sessions synced."
            else
                echo "WARNUNG: Codex-Sitzungen nicht uebertragen"
            fi
        fi
    else
        echo "No Codex sessions found in $CODEX_DIR."
    fi
else
    echo "Skipping Codex: $CODEX_DIR not found."
fi

if [ "$FEHLER" -gt 0 ]; then
    exit 1
fi

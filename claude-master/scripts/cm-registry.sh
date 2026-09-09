#!/usr/bin/env bash
# claude-master registry — fotografa le sessioni Claude vive per il ripristino dopo un riavvio.
#
# Quando la macchina si riavvia (spesso per saturazione della RAM) le sessioni
# muoiono tutte e nessuno ricorda quali erano. Questo file lo ricorda. Lo
# aggiornano `launch` a ogni lancio (T52), gli hook di sessione (fase 4) e un
# cron di riconciliazione; `restore` lo legge.
#
# Fonte: `cm-sessions.py --json --no-screen` (registro peer + /proc). Per ogni
# sessione con un nome tmux: nome, cartella, account. Formato uguale al
# registro legacy (`salvato`, `sessioni[] {nome, cartella, account}`), ma con
# i nomi degli account della configurazione: per questo il file NON e' condiviso
# con `sessioni-vive.json` durante la prova (il ripristino legacy legge
# «aziendale», il plugin legge i suoi nomi).
#
# Guardia (T19, 07/09/2026): se non trova nessuna sessione NON sovrascrive il
# registro. Dopo un riavvio il cron dei 5 minuti girava prima del ripristino e
# svuotava la lista che il ripristino doveva leggere.
#
# Uso: claude-master registry            scrive il file
#      claude-master registry --show     stampa il contenuto corrente   (it: --mostra)
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
REG="$CM_REGISTRY_FILE"
case "${1:-}" in
  --show|--mostra) cat "$REG" 2>/dev/null || cm_msg registry.none; exit 0 ;;
esac
command -v tmux >/dev/null || exit 0
mkdir -p "$(dirname "$REG")"
TMP="$REG.tmp.$$"
python3 "$CM_SCRIPTS/cm-sessions.py" --json --no-screen 2>/dev/null | python3 -c '
import json, sys, time
rows = [r for r in json.load(sys.stdin) if r.get("tmux") and r.get("cwd")]
out = {"salvato": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
       "sessioni": [{"nome": r["tmux"], "cartella": r["cwd"], "account": r["account"]} for r in rows]}
print(json.dumps(out, ensure_ascii=False, indent=1))
' > "$TMP"
if grep -q '"nome"' "$TMP"; then mv "$TMP" "$REG"; else rm -f "$TMP"; fi

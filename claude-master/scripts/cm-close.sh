#!/usr/bin/env bash
# claude-master close — chiude una sessione Claude per davvero.
#
#   claude-master close <nome-tmux>              chiude quella sessione
#   claude-master close --abandoned [--dry-run]  quelle staccate E ferme su una domanda   (it: --abbandonate --prova)
#
# Esiste perche' le sessioni sopravvivono alla chiusura della finestra: una
# finestra e' una vista, la sessione vive finche' non la chiudi tu. Non chiude mai
# una sessione ATTACCATA (T14): se qualcuno la sta guardando, la sta probabilmente
# usando. E una sessione non puo' chiudere se stessa in modo pulito: il kill
# arriverebbe a turno aperto (per quello c'e' `restart`).
#
# `kill-session -t =NOME` (T1/T50): senza `=` tmux aggancia per prefisso e con i
# nomi -2/-3 uccide la sessione sbagliata — l'08/09/2026 e' morta `fable-director-2`.
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"

PROVA=no; BERSAGLIO=""; ABBANDONATE=no
for arg in "$@"; do
  case "$arg" in
    --dry-run|--prova) PROVA=si ;;
    --abandoned|--abbandonate) ABBANDONATE=si ;;
    -*) cm_msg close.unknown_option "opt=$arg" >&2; exit 2 ;;
    *) BERSAGLIO="$arg" ;;
  esac
done

attaccata() {
  [ "$(cm_tmux list-sessions -F '#{session_name} #{session_attached}' 2>/dev/null | awk -v n="$1" '$1 == n {print $2}')" = "1" ]
}
chiudi() {
  if [ "$PROVA" = si ]; then echo "  $(cm_msg close.would_close "name=$1")"; return; fi
  cm_tmux kill-session -t "=$1" 2>/dev/null && echo "  $(cm_msg close.closed "name=$1")"
}

if [ "$ABBANDONATE" = si ]; then
  # stessa regola di `sessions`: flag dell'hook o due indizi sullo schermo, e nessuno attaccato
  trovate=0
  while read -r nome; do
    [ -n "$nome" ] || continue
    chiudi "$nome"; trovate=$((trovate + 1))
  done < <(python3 "$CM_SCRIPTS/cm-sessions.py" --json | python3 -c '
import json, sys
for r in json.load(sys.stdin):
    if r.get("tmux") and r.get("waiting") and not r.get("attached"):
        print(r["tmux"])')
  [ "$trovate" -eq 0 ] && echo "  $(cm_msg close.none_abandoned)"
  exit 0
fi

if [ -z "$BERSAGLIO" ]; then
  cm_msg close.usage >&2
  echo "  $(cm_msg close.running): $(cm_tmux list-sessions -F '#{session_name}' 2>/dev/null | tr '\n' ' ')" >&2
  exit 2
fi
cm_tmux has-session -t "=$BERSAGLIO" 2>/dev/null || {
  cm_msg close.missing "name=$BERSAGLIO" >&2
  echo "  $(cm_msg close.running): $(cm_tmux list-sessions -F '#{session_name}' 2>/dev/null | tr '\n' ' ')" >&2
  exit 3
}
if attaccata "$BERSAGLIO" && [ "$PROVA" = no ]; then
  cm_msg close.attached "name=$BERSAGLIO" >&2
  exit 4
fi
chiudi "$BERSAGLIO"

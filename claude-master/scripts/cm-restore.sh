#!/usr/bin/env bash
# claude-master restore — rilancia le sessioni registrate prima di un riavvio.
#
#   claude-master restore            rilancia: con un terminale chiede conferma (N s poi si'),
#                                    SENZA terminale si ferma alla lista (serve --yes)
#   claude-master restore --dry-run  dice cosa farebbe                                  (it: --prova)
#   claude-master restore --yes      non chiede                                          (it: --si)
#
# Legge il registro (`registry.file`, aggiornato dagli hook di sessione e da `launch`;
# il cron lo riconcilia). Ogni sessione riparte con --continue (riprende la sua ultima
# conversazione) e la sua finestra, in parallelo; quella della radice (`restore.last`)
# per ultima: e' quella a cui ci si attacca. Se una sessione con quel nome e' gia' viva
# la salta: si puo' rilanciare a meta'. Le cartelle sparite si saltano e si dicono.
#
# Le sessioni PARCHEGGIATE non sono nel registro (park le chiude e l'hook SessionEnd
# aggiorna il registro): restano parcheggiate, si riprendono con `unpark`.
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
REG="$CM_REGISTRY_FILE"
GOOD="$CM_REGISTRY_GOOD_FILE"
PROVA=no; CONFERMA=si
for a in "$@"; do case "$a" in --dry-run|--prova) PROVA=si ;; --yes|--si) CONFERMA=no ;; esac; done
[ -f "$REG" ] || [ -f "$GOOD" ] || { cm_msg restore.no_registry "path=$REG"; exit 1; }
LOGDIR="$CM_STATE_DIR/restore"; mkdir -p "$LOGDIR"

# unione del registro riconciliato e della fotografia «ultimo insieme buono» (11/09/2026): una
# sessione chiusa a mano prima del riavvio sta solo nella fotografia; si dice da dove viene ciascuna
mapfile -t RIGHE < <(python3 - "$REG" "$GOOD" "$MSG_RESTORE_SRC_REGISTRY" "$MSG_RESTORE_SRC_SNAPSHOT" <<'PY'
import json, sys
def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}
reg, good = load(sys.argv[1]), load(sys.argv[2])
seen = set()
for s in reg.get("sessioni", []):
    seen.add(s["nome"])
    print(f'{s["nome"]}\t{s["cartella"]}\t{s.get("account", "")}\t' + sys.argv[3].format(t=str(reg.get("salvato", ""))[:16].replace("T", " ")))
for s in good.get("sessioni", []):
    if s["nome"] in seen:
        continue
    print(f'{s["nome"]}\t{s["cartella"]}\t{s.get("account", "")}\t' + sys.argv[4].format(t=str(s.get("visto", ""))[:16].replace("T", " ")))
PY
)
[ "${#RIGHE[@]}" -gt 0 ] || { cm_msg restore.empty; exit 1; }
SALVATO=$(python3 -c "import json,sys
for p in sys.argv[1:]:
    try: print(json.load(open(p)).get('salvato','')[:16]); break
    except Exception: pass" "$REG" "$GOOD")

DA_FARE=()
for r in "${RIGHE[@]}"; do
  IFS=$'\t' read -r nome cartella acct fonte <<<"$r"
  if cm_tmux has-session -t "=$nome" 2>/dev/null; then echo "  $(cm_msg restore.alive "name=$nome")"; continue; fi
  [ -d "$cartella" ] || { echo "  $(cm_msg restore.gone "name=$nome" "dir=$cartella")"; continue; }
  DA_FARE+=("$r")
done
[ "${#DA_FARE[@]}" -gt 0 ] || { cm_msg restore.nothing; exit 0; }

cm_msg restore.todo "n=${#DA_FARE[@]}" "saved=$SALVATO"
for r in "${DA_FARE[@]}"; do IFS=$'\t' read -r nome cartella acct fonte <<<"$r"; printf '  %-28s %-13s %-40s %s\n' "$nome" "$acct" "$cartella" "$fonte"; done
# modalita' scheda (terminal.open_as_tab, 11/09/2026): tutte in UNA finestra del Terminale, quindi in
# SEQUENZA (una duplicazione per volta, ~20 s l'una), non in parallelo
A_SCHEDE=no
if [ "${CM_TERMINAL_OPEN_AS_TAB:-true}" = true ] && [ "$("$CM_SCRIPTS/cm-terminal.sh" detect)" = chromeos ]; then
  A_SCHEDE=si
  IFS=$'\t' read -r nome cartella acct fonte <<<"${DA_FARE[0]}"
  piano=$(python3 "$CM_SCRIPTS/cm-tile.py" open-tab "$nome" --dry-run 2>/dev/null) && echo "  $(cm_msg restore.as_tabs "plan=$piano")"
fi
[ "$PROVA" = si ] && exit 0
if [ "$CONFERMA" = si ]; then
  if [ -t 0 ]; then
    read -r -t "$CM_RESTORE_CONFIRM_TIMEOUT_S" -p "$(cm_msg restore.confirm "s=$CM_RESTORE_CONFIRM_TIMEOUT_S") " risp || risp=s
    case "${risp:-s}" in n|N) cm_msg restore.aborted; exit 0 ;; esac
  else
    # senza terminale nessuno puo' rispondere: si ferma alla lista (come --dry-run) e dice
    # come eseguire davvero. Un `restore` letto «per vedere» da una sessione Claude rilanciava
    # tutto (master, 09/09/2026 23:20).
    cm_msg restore.no_tty
    exit 0
  fi
fi

# tutte in parallelo tranne l'ultima (restore.last), che va per ultima
ULTIMA=""
for r in "${DA_FARE[@]}"; do
  IFS=$'\t' read -r nome cartella acct fonte <<<"$r"
  if [ "$nome" = "$CM_RESTORE_LAST" ]; then ULTIMA="$r"; continue; fi
  opz=(--continue); [ -n "$acct" ] && opz+=(--account "$acct")
  if [ "$A_SCHEDE" = si ]; then
    "$CM_SCRIPTS/cm-launch.sh" "$cartella" "${opz[@]}" >"$LOGDIR/$nome.log" 2>&1 \
      && echo "  $(cm_msg restore.ok "name=$nome")" || echo "  $(cm_msg restore.failed "name=$nome" "log=$LOGDIR/$nome.log")"
  else
    ( "$CM_SCRIPTS/cm-launch.sh" "$cartella" "${opz[@]}" >"$LOGDIR/$nome.log" 2>&1 \
        && echo "  $(cm_msg restore.ok "name=$nome")" || echo "  $(cm_msg restore.failed "name=$nome" "log=$LOGDIR/$nome.log")" ) &
  fi
done
wait
if [ -n "$ULTIMA" ]; then
  IFS=$'\t' read -r nome cartella acct fonte <<<"$ULTIMA"
  opz=(--continue); [ -n "$acct" ] && opz+=(--account "$acct")
  "$CM_SCRIPTS/cm-launch.sh" "$cartella" "${opz[@]}" >"$LOGDIR/$nome.log" 2>&1 \
    && echo "  $(cm_msg restore.ok "name=$nome")" || echo "  $(cm_msg restore.failed "name=$nome" "log=$LOGDIR/$nome.log")"
fi
cm_msg restore.done; cm_tmux list-sessions -F '  #{session_name}' 2>/dev/null
# il bot del telefono riparte subito col ripristino, senza aspettare il cron (mandato 11/09 18:30)
[ "${CM_BOT_ENABLED:-false}" = true ] && python3 "$CM_SCRIPTS/cm-bot.py" ensure >/dev/null 2>&1 || true

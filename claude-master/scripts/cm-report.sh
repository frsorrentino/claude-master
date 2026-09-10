#!/usr/bin/env bash
# claude-master report — inoltra una segnalazione (screenshot + testo) alla sessione del progetto.
#
# E' il gesto piu' frequente dal telefono: arriva uno screenshot di un cliente,
# va archiviato nel progetto giusto e la sessione di quel progetto deve
# riceverlo con il testo. Tre passi che diventano uno.
#
# Uso: claude-master report <progetto> <immagine|-> "testo" [--no-launch]     (it: --senza-lancio)
#   <progetto>   nome o pezzo del nome della cartella (sito, cliente-a): si cerca in
#                `workspace.project_dirs` sotto la radice, prima esatto, poi prefisso, poi sottostringa
#   <immagine>   percorso del file (png/jpg/webp/gif); "-" per nessuna immagine
#   --no-launch  se la sessione non c'e', non la lancia: archivia e basta
#
# Copia l'immagine in `<progetto>/<report.subdir>/<data>-<slug>.<ext>` (chmod 644,
# cosi' la sessione la legge), trova la sessione tmux con lo stesso nome che
# darebbe `launch` (o la lancia, account dedotto dal percorso) e le consegna il
# testo con il percorso dell'immagine via `talk --no-wait` (socket se c'e', tmux
# altrimenti). Non aspetta la risposta.
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"

PROGETTO="${1:-}"; IMG="${2:-}"; TESTO="${3:-}"; LANCIO=si
for a in "${@:4}"; do case "$a" in --no-launch|--senza-lancio) LANCIO=no ;; esac; done
[ -n "$PROGETTO" ] && [ -n "$IMG" ] && [ -n "$TESTO" ] || { cm_msg report.usage >&2; exit 2; }
ROOT="$(eval echo "$CM_WORKSPACE_ROOT")"

trova() {  # le cartelle di progetto candidate (figlie dirette di ogni project_dir)
  local d
  for d in $CM_WORKSPACE_PROJECT_DIRS; do
    find "$ROOT/$d" -mindepth 1 -maxdepth 1 -type d 2>/dev/null
  done | grep -v -E "/($(echo "$CM_WORKSPACE_EXCLUDED_DIRS" | tr ' ' '|'))$" | sort -u
}
CAND=$(trova | grep -iE "/$PROGETTO$" | head -1)
[ -n "$CAND" ] || CAND=$(trova | grep -iE "/$PROGETTO[^/]*$" | head -1)
[ -n "$CAND" ] || CAND=$(trova | grep -iE "/[^/]*$PROGETTO[^/]*$" | head -1)
if [ -z "$CAND" ]; then
  cm_msg report.no_project "project=$PROGETTO" >&2
  echo "  $(trova | grep -i "${PROGETTO:0:3}" | xargs -n1 basename 2>/dev/null | tr '\n' ' ')" >&2
  exit 3
fi
CARTELLA="$CAND"
NOME_CARTELLA=$(basename "$CARTELLA")

# --- archivio dell'immagine -------------------------------------------------------
PERCORSO_IMG=""
if [ "$IMG" != "-" ]; then
  [ -f "$IMG" ] || { cm_msg report.no_image "path=$IMG" >&2; exit 4; }
  EXT="${IMG##*.}"; EXT="${EXT,,}"
  case " $CM_REPORT_IMAGE_EXTS " in *" $EXT "*) ;; *) EXT=png ;; esac
  SLUG=$(printf '%s' "$TESTO" | tr -cs 'A-Za-z0-9àèéìòù' '-' | tr 'A-Z' 'a-z' | cut -c1-40 | sed 's/^-//; s/-$//')
  [ -n "$SLUG" ] || SLUG=report
  DEST_DIR="$CARTELLA/$CM_REPORT_SUBDIR"
  mkdir -p "$DEST_DIR"
  PERCORSO_IMG="$DEST_DIR/$(date +%Y-%m-%d)-$SLUG.$EXT"
  n=1; while [ -e "$PERCORSO_IMG" ]; do n=$((n+1)); PERCORSO_IMG="$DEST_DIR/$(date +%Y-%m-%d)-$SLUG-$n.$EXT"; done
  cp "$IMG" "$PERCORSO_IMG" && chmod 644 "$PERCORSO_IMG"
fi

# --- sessione del progetto: lo stesso nome che darebbe launch ------------------------
ACC="$CM_DEFAULT_ACCOUNT"; bestlen=0
while IFS=$'\t' read -r p a; do
  [ -n "$p" ] || continue
  case "$CARTELLA/" in "$p"/*) [ "${#p}" -gt "$bestlen" ] && { ACC="$a"; bestlen="${#p}"; } ;; esac
done <<<"$CM_FOLDER_MAP"
NOME="$(cm_get "$ACC" TMUX_PREFIX)$(printf '%s' "$NOME_CARTELLA" | sed 's/[^A-Za-z0-9]/-/g')"
if ! cm_tmux has-session -t "=$NOME" 2>/dev/null; then
  if [ "$LANCIO" = no ]; then
    cm_msg report.archived "path=${PERCORSO_IMG:-(-)}"
    cm_msg report.not_delivered "name=$NOME"
    exit 0
  fi
  cm_msg report.launching "name=$NOME" >&2
  "$CM_SCRIPTS/cm-launch.sh" "$CARTELLA" --no-window >&2 || { cm_msg report.launch_failed >&2; exit 5; }
fi

# --- consegna, senza aspettare la risposta ---------------------------------------
PROMPT="$(cm_msg report.prompt "date=$(date +%d/%m/%Y)" "text=$TESTO")"
[ -n "$PERCORSO_IMG" ] && PROMPT="$PROMPT
$(cm_msg report.prompt_image "path=$PERCORSO_IMG")"
NATIVO=no python3 "$CM_SCRIPTS/cm-talk.py" talk "$NOME" "$PROMPT" --no-wait --no-native-hint 2>"$CM_STATE_DIR/report-talk.err" || {
  cat "$CM_STATE_DIR/report-talk.err" >&2; exit 5; }
VIA=$(grep -o 'via [a-z]*' "$CM_STATE_DIR/report-talk.err" | head -1)
# T18: per la via tmux un testo lungo entra come "Pasted text" e Claude Code a volte
# non lo spedisce con l'Invio della stessa raffica: se e' rimasto nel campo, un
# secondo Invio lo manda. E' testo nostro, non un suggerimento.
if [ "$VIA" = "via tmux" ]; then
  sleep 3
  if cm_tmux capture-pane -p -t "$NOME" 2>/dev/null | grep -q 'Pasted text'; then cm_tmux send-keys -t "$NOME" Enter; fi
fi
cm_msg report.delivered "name=$NOME" "project=$NOME_CARTELLA" "via=${VIA#via }"
[ -n "$PERCORSO_IMG" ] && echo "  $(cm_msg report.image_line "path=$PERCORSO_IMG")"
cm_msg report.followup "name=$NOME"

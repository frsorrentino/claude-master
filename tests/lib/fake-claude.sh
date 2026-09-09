#!/usr/bin/env bash
# claude finto per i test di launch/close/restart: disegna le schermate che
# Claude Code 2.1.26x mostra davvero (fixture catturate dal vivo) e risponde ai
# tasti come lui. Nessun modello, nessuna rete.
#
# Scenario da FAKE_CLAUDE_SCENARIO (default "plain"), piu' d'uno separati da virgola:
#   trust         prima chiede la fiducia sulla cartella («❯ No, exit» / «Yes, I trust»): T3
#   trust-late    come trust ma la domanda compare dopo FAKE_CLAUDE_DELAY secondi (default 4): T4
#   bypass        dialogo «Bypass Permissions mode» (T61)
#   slow          schermo vuoto per FAKE_CLAUDE_DELAY secondi prima del prompt (T5)
#   die           esce subito con errore (sessione morta dopo l'avvio)
#   question      dopo il prompt mostra un menu numerato con «Enter to select» (T13)
#   plain         prompt subito
# Scrive il registro peer come Claude Code: $CLAUDE_CONFIG_DIR/sessions/<pid>.json
# (FAKE_CLAUDE_REGISTER=0 per non farlo). Stampa il link Remote Control se
# --remote-control e' fra gli argomenti. Registra gli argomenti ricevuti in
# FAKE_CLAUDE_ARGS_LOG (una riga per avvio) per i test sull'argv (--continue,
# --resume, -n, --remote-control, --bg).
set -u
# Lo scenario si legge da un FILE se FAKE_CLAUDE_SCENARIO_FILE e' impostata: le
# sessioni tmux ereditano l'ambiente del SERVER (quello del primo comando), non
# del chiamante, quindi una variabile cambiata fra un lancio e l'altro non arriva.
SCEN="${FAKE_CLAUDE_SCENARIO:-plain}"
[ -n "${FAKE_CLAUDE_SCENARIO_FILE:-}" ] && [ -f "$FAKE_CLAUDE_SCENARIO_FILE" ] && SCEN="$(cat "$FAKE_CLAUDE_SCENARIO_FILE")"
SCEN=",${SCEN},"
DELAY="${FAKE_CLAUDE_DELAY:-4}"
case "${1:-}" in agents|attach|logs|stop) exit 1 ;; esac
case " $* " in *" --cloud "*|*" -p "*) [ -n "${FAKE_CLAUDE_ARGS_LOG:-}" ] && printf '%s\n' "$*" >> "$FAKE_CLAUDE_ARGS_LOG"; echo "Sent to cloud session (fake)"; exit 0 ;; esac
if [ "${1:-}" = remote-control ]; then [ -n "${FAKE_CLAUDE_ARGS_LOG:-}" ] && printf '%s\n' "$*" >> "$FAKE_CLAUDE_ARGS_LOG"; echo "Remote Control server (fake) https://claude.ai/code/session_01FAKEDESK"; sleep 600; exit 0; fi
if [ -n "${FAKE_CLAUDE_ARGS_LOG:-}" ]; then
  ev=""; [ -n "${FAKE_CLAUDE_ECHO_ENV:-}" ] && ev=" ENV $FAKE_CLAUDE_ECHO_ENV=${!FAKE_CLAUDE_ECHO_ENV:-}"
  printf '%s%s\n' "$*" "$ev" >> "$FAKE_CLAUDE_ARGS_LOG"
fi
NAME=""; RC=""; BG=no; args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    -n|--name) NAME="${args[$((i+1))]:-}" ;;
    --remote-control) RC="${args[$((i+1))]:-auto}" ;;
    --bg|--background) BG=yes ;;
    --version|-v) echo "2.1.265 (fake)"; exit 0 ;;
  esac
done

if [ "$BG" = yes ]; then
  echo "Background session started: bg-$$"
  echo "  claude attach bg-$$"
  exit 0
fi

case "$SCEN" in *,die,*) echo "Error: cannot start (fake die)" >&2; exit 1 ;; esac

register() {
  [ "${FAKE_CLAUDE_REGISTER:-1}" = 1 ] || return 0
  local dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions" ps tm
  mkdir -p "$dir"
  ps=$(awk '{print $22}' /proc/$$/stat)
  tm=$(tmux display-message -p '#S:@#{window_index}.%#{pane_index}' 2>/dev/null || echo "")
  printf '{"pid":%s,"sessionId":"fake-%s","cwd":"%s","startedAt":%s,"procStart":"%s","version":"2.1.265","kind":"interactive","tmux":"%s","messagingSocketPath":"/tmp/fake-%s.sock","name":"%s","status":"idle","bridgeSessionId":"%s"}\n' \
    "$$" "$$" "$PWD" "$(date +%s)000" "$ps" "$tm" "$$" "${NAME:-$(basename "$PWD")}" "${RC:+session_01FAKE$$}" > "$dir/$$.json"
  printf '{"peerToken":"faketoken%s","procStart":"%s"}\n' "$$" "$ps" > "$dir/$$.fakehash.key"
  trap 'rm -f "$dir/$$.json" "$dir/$$.fakehash.key"; exit' EXIT INT TERM HUP   # anche su kill-server (SIGHUP)
}

dialog() {  # $1 = testo della domanda, $2 = voce si'
  printf '\033[2J\033[H'   # Claude Code ridisegna lo schermo: il dialogo precedente sparisce
  printf '\n %s\n ❯ No, exit\n   %s\n Enter to confirm · Esc to cancel\n' "$1" "$2"
  local sel=0 k
  while IFS= read -rsn1 k; do
    case "$k" in
      $'\x1b') read -rsn2 -t 0.2 k2 2>/dev/null || true
               case "${k2:-}" in '[B') sel=1 ;; '[A') sel=0 ;; esac
               [ "$sel" = 1 ] && printf '\n   No, exit\n ❯ %s\n' "$2" || printf '\n ❯ No, exit\n   %s\n' "$2" ;;
      "")      if [ "$sel" = 1 ]; then return 0; else echo "exit (No)"; exit 0; fi ;;
      1)       echo "exit (typed 1 = No)"; exit 0 ;;
      2)       return 0 ;;
    esac
  done
}

case "$SCEN" in *,trust-late,*) sleep "$DELAY";; esac
case "$SCEN" in *,trust,*|*,trust-late,*)
  dialog "Quick safety check: Is this a project you created or one you trust? (Like your own code, a well-known open source project, or work from your team). If not, take a moment to review what's in this folder first." "Yes, I trust this folder" ;;
esac
case "$SCEN" in *,bypass,*)
  dialog "WARNING: Claude Code running in Bypass Permissions mode" "Yes, I accept" ;;
esac
case "$SCEN" in *,slow,*) sleep "$DELAY";; esac

register
clear 2>/dev/null || true
echo " ▐▛███▛█   Claude Code v2.1.265 (fake)"
[ -n "$RC" ] && echo "▎ Keep working from anywhere: https://claude.ai/code/session_01FAKE$$"
echo "──────────────────────────── ${NAME:-$(basename "$PWD")} ─"
case "$SCEN" in *,question,*)
  printf '\n ☐ Colore\ncolore preferito?\n❯ 1. rosso\n  2. blu\nEnter to select · ↑/↓ to navigate · Esc to cancel\n' ;;
esac
printf '❯ '   # senza newline: il testo digitato resta sulla riga del prompt, come in Claude Code
# resta vivo finche' non riceve /exit (o un segnale)
while IFS= read -r line; do
  line="${line//$'\e'/}"   # un ESC arrivato prima del testo (Escape di talk) non e' testo
  case "$line" in /exit|/quit) echo "bye"; exit 0 ;; esac
  printf '> %s\n❯ ' "$line"
done

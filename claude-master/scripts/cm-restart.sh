#!/usr/bin/env bash
# claude-master restart — riavvia la sessione Claude corrente riprendendone la conversazione.
#
#   claude-master restart arm                  arma il riavvio (da dentro la sessione da riavviare)
#   claude-master restart arm --clean          riparte SENZA -c: contesto azzerato, memoria intatta  (it: --pulita)
#   claude-master restart arm --switch-account [NOME]
#                                              riparte sull'ALTRO account riprendendo la STESSA conversazione:
#                                              il transcript e' un file locale, viene copiato nella cartella
#                                              projects dell'altro account e ripreso con --resume. ATTENZIONE:
#                                              da quel momento il contesto va all'API sotto l'altra
#                                              organizzazione (scelta dell'utente, T51).  (it: --altro-account)
#   claude-master restart hook                 chiamato dallo Stop hook: se armato, stacca l'esecutore
#   claude-master restart failed               chiamato da StopFailure: il flag armato resta, marcato fallito
#   claude-master restart exec <flag>          l'esecutore staccato (non invocarlo a mano)
#   claude-master restart [list]               le sessioni armate (un flag per sessione)
#
# UN FLAG PER SESSIONE (11/09/2026 13:10: otto `arm` in due minuti, quattro riavvii — un
# solo file `restart.flag_file`, l'ultimo arm sovrascriveva gli altri in silenzio): il file
# di ogni sessione e' `<flag_file senza .json>-<nome tmux>.json`; lo Stop hook legge SOLO il
# proprio.
#
# Perche' passare dallo Stop hook invece di uccidere e basta (T15): un kill
# lanciato da un tool del modello arriva a TURNO APERTO, e il transcript resta
# con una chiamata senza risposta — `claude -c` poi riprende una conversazione
# monca. Stop scatta a turno finito, con il transcript gia' scritto: e' l'unico
# istante in cui chiudere e' sicuro. Il flag e' una richiesta, l'hook e' l'esecutore.
#
# L'esecutore deve sopravvivere alla morte del riquadro tmux da cui nasce:
# `setsid` lo stacca. Il flag viene consumato PRIMA di staccare l'esecutore:
# meglio un riavvio mancato che un riavvio in loop a ogni turno. Un token di
# generazione nel flag evita che due hook rilancino due volte (1.9).
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
FLAG="$CM_RESTART_FLAG_FILE"
LOG="$CM_RESTART_LOG"
mkdir -p "$(dirname "$FLAG")" "$(dirname "$LOG")"

flag_of() { printf '%s-%s.json' "${FLAG%.json}" "$1"; }
json_get() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=d.get(sys.argv[2],""); print(v if not isinstance(v,bool) else str(v).lower())' "$1" "$2" 2>/dev/null; }
my_tmux() { cm_tmux display-message -p -t "${TMUX_PANE:-}" '#S' 2>/dev/null; }

# ---------------------------------------------------------------- arm
arm() {
  [ -n "${TMUX:-}" ] || { cm_msg restart.not_in_tmux >&2; exit 3; }
  local info nome pid cartella
  info=$(cm_tmux display-message -p -t "${TMUX_PANE:-}" '#S|#{pane_pid}' 2>/dev/null) || { cm_msg restart.no_tmux_id >&2; exit 3; }
  nome="${info%%|*}"; pid="${info##*|}"; cartella="$(pwd -P)"
  [ -n "$nome" ] && [ -n "$pid" ] || { cm_msg restart.no_tmux_id >&2; exit 3; }
  local clean=false conf_da="" conf_a="" sid="" acc_da="" acc_a=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --clean|--pulita) clean=true ;;
      --switch-account|--altro-account)
        conf_da="$(readlink -f "${CLAUDE_CONFIG_DIR:-$(eval echo ~)/.claude}")"
        sid="${CLAUDE_CODE_SESSION_ID:-}"
        [ -n "$sid" ] || { cm_msg restart.no_session_id >&2; exit 3; }
        for a in $CM_ACCOUNTS_KEYS; do
          [ "$(readlink -f "$(cm_get "$a" CONFIG_DIR)")" = "$conf_da" ] && acc_da="$a"
        done
        if [ -n "${2:-}" ] && [ "${2#-}" = "$2" ]; then acc_a="$2"; shift
        else  # due account: l'altro; di piu': va detto
          for a in $CM_ACCOUNTS_KEYS; do [ "$a" != "$acc_da" ] && { [ -n "$acc_a" ] && acc_a="?" || acc_a="$a"; }; done
          [ "$acc_a" = "?" ] && { cm_msg restart.which_account "known=$CM_ACCOUNTS_KEYS" >&2; exit 3; }
        fi
        conf_a="$(cm_get "$acc_a" CONFIG_DIR)"; [ -n "$conf_a" ] || { cm_msg launch.unknown_account "account=$acc_a" "known=$CM_ACCOUNTS_KEYS" >&2; exit 3; } ;;
      *) cm_msg restart.unknown_option "opt=$1" >&2; exit 2 ;;
    esac
    shift
  done
  python3 - "$(flag_of "$nome")" "$nome" "$pid" "$cartella" "$clean" "$conf_da" "$conf_a" "$sid" "$acc_a" <<'PY'
import json, sys, time, uuid
f, nome, pid, cartella, clean, conf_da, conf_a, sid, acc_a = sys.argv[1:]
json.dump({"tmux": nome, "pid": int(pid), "cartella": cartella, "armato": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "gen": uuid.uuid4().hex[:8], "pulita": clean == "true", "conf_da": conf_da, "conf_a": conf_a,
           "sessione": sid, "account_a": acc_a}, open(f, "w"))
PY
  cm_msg restart.armed "name=$nome" "pid=$pid" "dir=$cartella"
  [ -n "$conf_a" ] && cm_msg restart.armed_switch "account=$acc_a" "sid=$sid"
  [ "$clean" = true ] && cm_msg restart.armed_clean
  cm_msg restart.armed_when "log=$LOG"
}

# ---------------------------------------------------------------- hook
hook() {
  local mio f suo
  mio=$(my_tmux); [ -n "$mio" ] || exit 0
  f=$(flag_of "$mio")
  # compatibilita': un flag unico armato dalla versione precedente, se e' di questa sessione
  [ -f "$f" ] || { [ -f "$FLAG" ] && [ "$(json_get "$FLAG" tmux)" = "$mio" ] && f="$FLAG"; }
  [ -f "$f" ] || exit 0
  suo=$(json_get "$f" tmux)
  # Il flag e' di questa sessione? Altrimenti lo lascia a chi lo ha armato: due
  # sessioni aperte insieme non devono chiudersi a vicenda.
  [ "$mio" = "$suo" ] || exit 0
  local pending="$f.in-corso"
  mv "$f" "$pending" 2>/dev/null || exit 0
  setsid nohup "$CM_SCRIPTS/cm-restart.sh" exec "$pending" </dev/null >>"$LOG" 2>&1 &
  printf '{"systemMessage":"%s"}\n' "$(cm_msg restart.in_progress "log=$LOG" | sed 's/"/\\"/g')"
}

failed() {
  local f; f=$(flag_of "$(my_tmux)")
  [ -f "$f" ] || exit 0
  python3 - "$f" <<'PY'
import json, sys, time
f = sys.argv[1]; d = json.load(open(f)); d["fallito"] = time.strftime("%Y-%m-%dT%H:%M:%S%z"); json.dump(d, open(f, "w"))
PY
  echo "$(date -Is) StopFailure con riavvio armato: flag marcato fallito, non eseguito" >> "$LOG"
}

# ---------------------------------------------------------------- exec
# Vivo = esiste E non e' uno zombie: dopo SIGKILL il processo puo' restare <defunct> finche' il
# padre (tmux) non lo raccoglie, e kill -0 risponde ancora si' (visto il 10/09/2026 nella suite,
# «pid 1198 non muore»). Uno zombie e' morto per noi.
vivo() { kill -0 "$1" 2>/dev/null && ! grep -q '^State:.*Z' "/proc/$1/status" 2>/dev/null; }

esegui() {
  local flag="$1" nome pid cartella conf_da conf_a sid acc_a pulita gen
  nome=$(json_get "$flag" tmux); pid=$(json_get "$flag" pid); cartella=$(json_get "$flag" cartella)
  conf_da=$(json_get "$flag" conf_da); conf_a=$(json_get "$flag" conf_a); sid=$(json_get "$flag" sessione)
  acc_a=$(json_get "$flag" account_a); pulita=$(json_get "$flag" pulita); gen=$(json_get "$flag" gen)
  rm -f "$flag"
  echo "=== $(date -Is) riavvio di '$nome' ($cartella) gen=$gen ==="
  [ -n "$nome" ] && [ -n "$pid" ] && [ -n "$cartella" ] || { echo "flag illeggibile, annullato"; exit 1; }

  # 1. Uscita pulita: Esc svuota il campo (un residuo digitato manderebbe l'Invio a
  #    una riga diversa da /exit), poi il comando. Nome nudo per send-keys (T1).
  if cm_tmux has-session -t "=$nome" 2>/dev/null; then
    cm_tmux send-keys -t "$nome" Escape 2>/dev/null; sleep 1
    cm_tmux send-keys -t "$nome" "/exit" Enter 2>/dev/null
  fi
  for _ in $(seq 1 "$CM_RESTART_EXIT_WAIT_S"); do vivo "$pid" || break; sleep 1; done
  # 2. Scaletta: /exit -> TERM -> KILL. Il transcript e' gia' su disco (Stop e' passato).
  if vivo "$pid"; then echo "/exit non ha chiuso: SIGTERM"; kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 "$CM_RESTART_TERM_WAIT_S"); do vivo "$pid" || break; sleep 1; done; fi
  if vivo "$pid"; then echo "ancora vivo: SIGKILL"; kill -KILL "$pid" 2>/dev/null; sleep 2; fi
  if vivo "$pid"; then echo "pid $pid non muore, riavvio annullato"; exit 1; fi
  # 3. Il nome tmux va liberato prima di rilanciare, o launch battezza la nuova '<nome>-2'.
  cm_tmux kill-session -t "=$nome" 2>/dev/null
  for _ in $(seq 1 10); do cm_tmux has-session -t "=$nome" 2>/dev/null || break; sleep 1; done

  # 4. Rilancio. Un solo retry: se fallisce due volte il problema non e' transitorio.
  local -a OPZ=(--continue)
  [ "$pulita" = true ] && OPZ=()
  if [ -n "$conf_a" ] && [ -n "$sid" ]; then
    # Cambio account (T51): il transcript (e la sua cartella di contorno) passa nella
    # projects dell'altro account, poi --resume per id: -c prenderebbe l'ultima
    # conversazione dell'ALTRO account.
    local slug src dst
    slug=$(printf '%s' "$cartella" | sed 's|[^A-Za-z0-9]|-|g')
    src="$conf_da/projects/$slug/$sid.jsonl"; dst="$(eval echo "$conf_a")/projects/$slug"
    [ -f "$src" ] || { echo "!!! transcript non trovato: $src — riavvio annullato"; exit 1; }
    mkdir -p "$dst" && cp -f "$src" "$dst/" || { echo "!!! copia del transcript fallita"; exit 1; }
    [ -d "$conf_da/projects/$slug/$sid" ] && cp -rf "$conf_da/projects/$slug/$sid" "$dst/" 2>/dev/null
    echo "transcript copiato in $dst/$sid.jsonl ($(du -h "$src" | cut -f1))"
    OPZ=(--resume "$sid" --account "$acc_a")
  fi
  # 5. Display (14/09/2026): il server tmux ripartito al boot del 12/09 senza DISPLAY/WAYLAND_DISPLAY
  #    li nega a claude e quindi a questo esecutore; cm-terminal (T57) saltava la finestra, 9 riavvii
  #    su 9 senza finestra. Se il compositor c'e' (socket vivo), si recupera. Anche XDG_RUNTIME_DIR:
  #    manca pure quello, e senza WAYLAND_DISPLAY relativo non trova il socket.
  if [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
    local rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    [ -S "$rt/wayland-0" ] && export XDG_RUNTIME_DIR="$rt" WAYLAND_DISPLAY=wayland-0
    [ -S "${CM_X11_SOCKET_DIR:-/tmp/.X11-unix}/X0" ] && export DISPLAY=:0
    [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && echo "display recuperato: WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-}"
  fi
  local out rc
  out=$("$CM_SCRIPTS/cm-launch.sh" "$cartella" "${OPZ[@]}" 2>&1); rc=$?
  if [ $rc -ne 0 ]; then echo "primo rilancio fallito (rc=$rc):"; echo "$out"; sleep 5
    out=$("$CM_SCRIPTS/cm-launch.sh" "$cartella" "${OPZ[@]}" 2>&1); rc=$?; fi
  echo "$out"
  if [ $rc -ne 0 ]; then echo "!!! RIAVVIO FALLITO — nessuna sessione attiva su $cartella"; echo "!!! a mano: cd $cartella && claude -c"; exit 1; fi
  echo "=== riavvio completato $(date -Is) ==="
}

elenco() {
  local n=0 f
  for f in "${FLAG%.json}"-*.json "$FLAG"; do
    [ -f "$f" ] || continue
    n=$((n + 1))
    printf '  %-28s %s  %s%s%s\n' "$(json_get "$f" tmux)" "$(json_get "$f" armato | cut -c1-16)" "$(json_get "$f" cartella)" \
      "$([ "$(json_get "$f" pulita)" = true ] && printf ' --clean')" "$([ -n "$(json_get "$f" account_a)" ] && printf ' --switch-account %s' "$(json_get "$f" account_a)")"
  done
  [ "$n" -gt 0 ] || cm_msg restart.list_none
}

case "${1:-}" in
  arm|arma) shift; arm "$@" ;;
  list|elenca|"") elenco ;;
  hook)     hook ;;
  failed)   failed ;;
  exec|esegui) shift; esegui "${1:?serve il file flag}" ;;
  *) cm_msg restart.usage >&2; exit 2 ;;
esac

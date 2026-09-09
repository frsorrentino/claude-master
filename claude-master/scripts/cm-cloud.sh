#!/usr/bin/env bash
# claude-master cloud / follow / desk — nuvola e sportello (N4, N9).
#
#   claude-master cloud <cartella> "task" [--account N]   sessione cloud (claude --cloud) dall'account
#                                                         dedotto dalla cartella: il cwd deve avere un
#                                                         remote GitHub raggiungibile (o < 100 MB: bundle)
#   claude-master follow <id|url> "messaggio"             accoda un messaggio a una sessione cloud (claude -p --cloud)
#   claude-master desk [start|stop|status] [--no-window]  «sportello»: `claude remote-control` in tmux nella
#                                                         radice dei workspaces, cosi' dal telefono si aprono
#                                                         sessioni su richiesta senza passare dalla master
#   (--teleport <id> e' un'opzione di `launch`: porta una sessione cloud in tmux con finestra e colore)
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
CLAUDE="${CM_CLAUDE_BIN:-$(command -v claude)}"

account_env() {  # stampa "CLAUDE_CONFIG_DIR=..." solo per gli account fuori da ~/.claude (T68)
  local conf; conf="$(cm_get "$1" CONFIG_DIR)"
  [ "$(readlink -f "$conf")" != "$(readlink -f "$(eval echo ~)/.claude")" ] && printf 'CLAUDE_CONFIG_DIR=%s' "$conf" || printf 'CM_LAUNCHED=1'
}
deduce() {
  local best="" bestlen=0 p a
  while IFS=$'\t' read -r p a; do [ -n "$p" ] || continue
    case "$1/" in "$p"/*) [ "${#p}" -gt "$bestlen" ] && { best="$a"; bestlen="${#p}"; } ;; esac
  done <<<"$CM_FOLDER_MAP"
  printf '%s' "${best:-$CM_DEFAULT_ACCOUNT}"
}

cmd_cloud() {
  local dir="${1:-}" task="${2:-}" acc=""
  shift 2 2>/dev/null || { cm_msg cloud.usage >&2; exit 2; }
  while [ $# -gt 0 ]; do case "$1" in --account) shift; acc="$1" ;; *) cm_msg launch.unknown_option "opt=$1" >&2; exit 2 ;; esac; shift; done
  [ -d "$dir" ] && [ -n "$task" ] || { cm_msg cloud.usage >&2; exit 2; }
  [ -n "$acc" ] || acc="$(deduce "$dir")"
  cm_msg cloud.starting "dir=$dir" "account=$(cm_get "$acc" LABEL)"
  ( cd "$dir" && env "$(account_env "$acc")" "$CLAUDE" --cloud "$task" )
}
cmd_follow() {
  local id="${1:-}" text="${2:-}"
  [ -n "$id" ] && [ -n "$text" ] || { cm_msg cloud.follow_usage >&2; exit 2; }
  "$CLAUDE" -p "$text" --cloud "$id"
}
cmd_desk() {
  local verb="${1:-start}" nome="${CM_DESK_NAME:-sportello}" window=true
  for a in "$@"; do [ "$a" = --no-window ] && window=false; done
  local dir; dir="$(eval echo "$CM_WORKSPACE_ROOT")"
  case "$verb" in
    status)
      if cm_tmux has-session -t "=$nome" 2>/dev/null; then cm_msg desk.running "name=$nome"; cm_tmux capture-pane -p -t "$nome" | grep -oE 'https://claude\.ai/code/session_[A-Za-z0-9]+' | head -1
      else cm_msg desk.stopped "name=$nome"; fi ;;
    stop)
      cm_tmux kill-session -t "=$nome" 2>/dev/null && cm_msg desk.stopped "name=$nome" || cm_msg desk.not_running "name=$nome" ;;
    start)
      cm_tmux has-session -t "=$nome" 2>/dev/null && { cm_msg desk.already "name=$nome"; exit 0; }
      cm_tmux new-session -d -s "$nome" -c "$dir" env "$(account_env "$CM_DEFAULT_ACCOUNT")" "$CLAUDE" remote-control \
        --name "$nome" --capacity "${CM_DESK_CAPACITY:-4}" --permission-mode "${CM_DESK_PERMISSION_MODE:-acceptEdits}"
      sleep 2
      cm_tmux has-session -t "=$nome" 2>/dev/null || { cm_msg desk.died "name=$nome" >&2; exit 5; }
      [ "$window" = true ] && "$CM_SCRIPTS/cm-terminal.sh" open "$nome"
      cm_msg desk.started "name=$nome" "dir=$dir" ;;
    *) cm_msg desk.usage >&2; exit 2 ;;
  esac
}

case "${CM_SUBCOMMAND:-${1:-}}" in
  cloud)  [ "${1:-}" = cloud ] && shift; cmd_cloud "$@" ;;
  follow) [ "${1:-}" = follow ] && shift; cmd_follow "$@" ;;
  desk|sportello) [ "${1:-}" = desk ] || [ "${1:-}" = sportello ] && shift; cmd_desk "$@" ;;
  *) cm_msg cloud.usage >&2; exit 2 ;;
esac

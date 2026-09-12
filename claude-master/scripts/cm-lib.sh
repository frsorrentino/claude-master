#!/usr/bin/env bash
# Funzioni comuni degli script bash di claude-master. Si fa `source`.
#
# Carica la configurazione UNA volta (export CM_* + MSG_*), poi:
#   cm_tmux ...              tmux con il socket giusto (CM_TMUX_ARGS nei test, tmux.socket in config)
#   cm_account_of_name NOME  account dal prefisso tmux (prefisso piu' lungo vince; "" = quello senza prefisso)
#   cm_get ACCOUNT CAMPO     campo di un account (CONFIG_DIR, TMUX_PREFIX, SHAPE, SHELL_COMMAND, LABEL)
#   cm_msg CHIAVE k=v ...    messaggio formattato nella lingua configurata
CM_SCRIPTS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# T81 (11/09/2026, dal polso di Franz): dal cron PATH=/usr/bin:/bin e `claude` (link in ~/.local/bin)
# non si trova: il bot rispondeva «AVVIO FALLITO». Ogni script passa di qui: si antepone una volta.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:/usr/local/bin:$PATH" ;;
esac
if [ -z "${CM_CONFIG_LOADED:-}" ]; then
  eval "$(python3 "$CM_SCRIPTS/cm-config.py" --sh --messages)"
  export CM_CONFIG_LOADED=1
fi

cm_tmux() {
  # shellcheck disable=SC2086
  if [ -n "${CM_TMUX_ARGS:-}" ]; then tmux $CM_TMUX_ARGS "$@"
  elif [ -n "${CM_TMUX_SOCKET:-}" ]; then tmux -L "$CM_TMUX_SOCKET" "$@"
  else tmux "$@"; fi
}

cm_get() {  # cm_get personale TMUX_PREFIX
  local v="CM_ACCOUNTS_${1^^}_${2}"
  v="${v//-/_}"
  printf '%s' "${!v:-}"
}

cm_account_of_name() {
  local nome="$1" best="" bestlen=-1 acc pfx
  for acc in $CM_ACCOUNTS_KEYS; do
    pfx="$(cm_get "$acc" TMUX_PREFIX)"
    if [ -z "$pfx" ]; then
      [ "$bestlen" -lt 0 ] && { best="$acc"; bestlen=0; }
    elif [ "${nome#"$pfx"}" != "$nome" ] && [ "${#pfx}" -gt "$bestlen" ]; then
      best="$acc"; bestlen="${#pfx}"
    fi
  done
  printf '%s' "${best:-$CM_DEFAULT_ACCOUNT}"
}

cm_msg() {
  python3 "$CM_SCRIPTS/cm-config.py" --msg "$@"
}

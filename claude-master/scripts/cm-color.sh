#!/usr/bin/env bash
# claude-master color — sceglie l'icona del titolo di scheda per una sessione tmux.
#
#   claude-master color <nome-tmux>      stampa "<icona> <etichetta>"
#
# Forma = account (dal prefisso del nome: `tabs`/`accounts.<n>.shape` in config,
# oggi tondo personale e quadrato professionale). Colore = quale sessione, e
# deve essere DIVERSO da quelli in uso.
#
# Il colore non si puo' scegliere guardando solo se stessi: un contatore globale
# non sa cosa e' ancora aperto e rida' lo stesso colore appena il giro si chiude,
# un hash del nome e' stabile ma non evita le collisioni. Serve un registro, e
# la lista delle sessioni vive come verita' su cosa e' occupato davvero (T25).
#
# Le voci di sessioni morte vengono potate a ogni giro: il registro non cresce e
# un colore liberato torna disponibile subito. Registro: `nome<TAB>indice`,
# stesso formato di ~/.claude/colori-sessione (condiviso nella prova, D4).
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"

NOME="${1:?uso: claude-master color <nome-tmux>}"
REG="$CM_TABS_COLOR_REGISTRY"
LOCK="$REG.lock"
mkdir -p "$(dirname "$REG")"

ACC="$(cm_account_of_name "$NOME")"
SHAPE="$(cm_get "$ACC" SHAPE)"
PFX="$(cm_get "$ACC" TMUX_PREFIX)"
palette_var="CM_TABS_COLORS_${SHAPE^^}"
FORME="${!palette_var:-}"
[ -n "$FORME" ] || FORME="$CM_TABS_COLORS_CIRCLE"
# conteggio con un array bash, non con `wc -w`: in locale C le emoji non
# contano come parole (N=0 → indice "nan" nel registro, visto il 09/09/2026)
read -ra ICONE <<<"$FORME"
N=${#ICONE[@]}
ETICHETTA="$NOME"
[ "$CM_TABS_STRIP_PREFIX" = true ] && [ -n "$PFX" ] && ETICHETTA="${NOME#"$PFX"}"

# Le forme di TUTTI i nomi del registro servono per la coppia forma+colore
# (oltre N vive il colore si ripete per forza): passo ad awk "prefisso=forma"
# per ogni account, dal prefisso piu' lungo al piu' corto.
MAPPA=""
for acc in $CM_ACCOUNTS_KEYS; do MAPPA="$MAPPA$(cm_get "$acc" TMUX_PREFIX)=$(cm_get "$acc" SHAPE)"$'\n'; done
MAPPA=$(printf '%s' "$MAPPA" | awk -F= '{print length($1) "\t" $0}' | sort -rn | cut -f2-)

# Lock con mkdir (T56): due schede aperte nello stesso istante sceglierebbero lo
# stesso colore leggendo il registro prima che l'altra lo scriva.
i=0
while ! mkdir "$LOCK" 2>/dev/null; do
  i=$((i + 1)); [ "$i" -gt 50 ] && break
  sleep 0.1
done
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

VIVE=$(cm_tmux list-sessions -F '#{session_name}' 2>/dev/null)
[ -f "$REG" ] || : > "$REG"

IDX=$(
  awk -v nome="$NOME" -v shape="$SHAPE" -v n="$N" -v vive="$VIVE" -v mappa="$MAPPA" '
    function shape_of(s,   k, p, i) {
      for (i = 1; i <= nm; i++) {
        p = mp[i]
        if (p == "" || substr(s, 1, length(p)) == p) return ms[i]
      }
      return "circle"
    }
    BEGIN {
      nm = split(mappa, righe, "\n")
      for (i = 1; i <= nm; i++) { split(righe[i], kv, "="); mp[i] = kv[1]; ms[i] = kv[2] }
      split(vive, v, "\n")
      for (k in v) if (v[k] != "") viva[v[k]] = 1
      viva[nome] = 1              # la propria sessione potrebbe non esistere ancora
    }
    # registro: nome \t indice
    { if ($1 in viva) { reg[$1] = $2; if ($1 != nome) {
        occupato[$2] = 1
        coppia[shape_of($1) "-" $2] = 1
      } } }
    END {
      if (nome in reg && !(reg[nome] in occupato)) { print reg[nome]; exit }
      for (i = 0; i < n; i++) if (!(i in occupato))            { print i; exit }
      # oltre N sessioni vive il colore si ripete per forza: si riusa quello con la
      # coppia forma+colore ancora libera, cosi restano distinguibili
      for (i = 0; i < n; i++) if (!((shape "-" i) in coppia)) { print i; exit }
      print length(reg) % n
    }
  ' "$REG"
)

# riscrive il registro: le vive tranne se stessa, piu' la propria voce
{
  awk -v nome="$NOME" -v vive="$VIVE" '
    BEGIN { split(vive, v, "\n"); for (k in v) if (v[k] != "") viva[v[k]] = 1 }
    $1 != nome && ($1 in viva) { print $1 "\t" $2 }
  ' "$REG"
  printf '%s\t%s\n' "$NOME" "$IDX"
} > "$REG.tmp.$$" && mv "$REG.tmp.$$" "$REG"

printf '%s %s\n' "${ICONE[$IDX]}" "$ETICHETTA"

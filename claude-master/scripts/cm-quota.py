#!/usr/bin/env python3
"""claude-master quota — quanto e' pieno il serbatoio di ciascun account, in un posto solo.

Gli account sono piani separati, quindi quote indipendenti. Finora l'unico modo
di sapere quale fosse piu' libero era iniziare un lavoro e vedere se si
schiantava contro il limite.

DA DOVE ARRIVA IL DATO: non lo chiediamo a nessuno. La statusline di
fable-director riceve `rate_limits` nel JSON di sessione e lo scrive in
`<quota.source>/quota-<hash>.json`, dove <hash> sono i primi 8 caratteri dello
sha256 di CLAUDE_CONFIG_DIR. Qui quei file si leggono e basta: nessuna
chiamata, nessun token, nessuna sessione avviata.

IL PREZZO DI QUESTA SCELTA, ED E' IL PUNTO: un file si aggiorna solo quando una
sessione DI QUELL'ACCOUNT rende la sua statusline. Se un account sta fermo due
giorni, il suo numero e' di due giorni fa. Un numero vecchio spacciato per
fresco e' peggio dell'ignoranza: si decide con sicurezza sulla cosa sbagliata.
Per questo l'eta' della lettura sta in tabella accanto al numero, e una
finestra gia' scaduta viene detta a chiare lettere.

Uso: cm-quota.py [--json]
"""
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)

SOURCE = Path(cm.expand(CFG["quota"]["source"]))
# Oltre questa eta' la lettura viene marcata. Due ore sono tarate sulla
# finestra da 5 ore: e' la piu' veloce a muoversi.
SOGLIA_VECCHIA = 2 * 3600


def eta_umana(secondi):
    m = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
    if secondi < 90:
        return m("quota.age_now")
    if secondi < 5400:
        return m("quota.age_min", n=round(secondi / 60))
    if secondi < 172800:
        return m("quota.age_hours", n=round(secondi / 3600))
    return m("quota.age_days", n=round(secondi / 86400))


def leggi(config_dir):
    """Stato della quota per un account, o il motivo per cui non c'e'.

    L'account si identifica dalla cartella di configurazione, non dal nome: e'
    lo stesso criterio con cui la statusline sceglie il file, quindi i due non
    possono divergere.
    """
    acct = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    f = SOURCE / f"quota-{acct}.json"
    if not f.is_file():
        return {"stato": "mai visto", "file": str(f)}
    try:
        d = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"stato": f"illeggibile ({e.__class__.__name__})", "file": str(f)}
    eta = time.time() - f.stat().st_mtime
    reset = d.get("weekly_resets_at")
    scaduta = bool(reset) and reset < time.time()
    return {
        "stato": "ok", "file": str(f),
        "cinque_ore_pct": d.get("five_hour_used_pct"),
        "settimana_pct": d.get("weekly_used_pct"),
        "reset_settimanale": reset,
        "eta_secondi": round(eta),
        "vecchia": eta > SOGLIA_VECCHIA,
        "finestra_scaduta": scaduta,
    }


GIORNI = {"it": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
          "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


def data_breve(epoch):
    d = datetime.fromtimestamp(epoch)
    g = GIORNI.get(CFG["language"], GIORNI["en"])[d.weekday()]
    return f"{g} {d.strftime('%d/%m %H:%M')}"


def pct(v):
    return "-" if v is None else f"{round(v)}%"


def main():
    letture = [(name, leggi(cm.expand(a["config_dir"]))) for name, a in CFG["accounts"].items()]
    if "--json" in sys.argv:
        print(json.dumps({n: q for n, q in letture}, indent=2))
        return 0
    m = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
    print()
    print(m("quota.header"))
    print(m("quota.header_rule"))
    for nome, q in letture:
        if q["stato"] != "ok":
            print(f"{nome:<14} {q['stato']}")
            continue
        r = data_breve(q["reset_settimanale"]) if q["reset_settimanale"] else "-"
        eta = eta_umana(q["eta_secondi"]) + ("  " + m("quota.old_mark") if q["vecchia"] else "")
        print(f"{nome:<14} {pct(q['cinque_ore_pct']):>7} {pct(q['settimana_pct']):>10}  {r:<17} {eta}")
    avvisi = [n for n, q in letture if q["stato"] == "ok" and q["finestra_scaduta"]]
    if avvisi:
        print()
        for n in avvisi:
            print("  " + m("quota.window_expired", account=n))
    if any(q["stato"] == "ok" and q["vecchia"] for _, q in letture):
        print()
        print("  " + m("quota.old_note"))
    if any(q["stato"] != "ok" for _, q in letture):
        print()
        print("  " + m("quota.missing_note"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

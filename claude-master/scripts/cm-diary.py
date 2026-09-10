#!/usr/bin/env python3
"""claude-master diary — il diario della giornata dal ledger degli hook (N7).

  claude-master diary [--date YYYY-MM-DD | --since ORE] [--send] [--json]
  claude-master diary install | uninstall | status     cron serale (diary.cron_time, default 20:00)

Legge `<state_dir>/ledger.jsonl` (una riga per evento: start, stop, waiting, end; lo scrive
`cm-hook.py`) e racconta, per sessione: progetto, account, quando è nata e quando è finita (o
«viva»), quanti turni (eventi stop), quante volte si è fermata su una domanda o un permesso
(waiting, con lo strumento), l'ultimo messaggio dell'assistente (`last` dello stop, già nel
ledger). Poi i totali per account. Con --send lo manda su Telegram alle chat di `allowFrom`
(stesso bot del plugin: sendMessage non confligge col polling, solo getUpdates è esclusivo).

Il *quanto è costato* non sta qui: sono le ricevute di fable-director. Qui c'è il *cosa è successo*.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
D = CFG["diary"]


def ledger_path():
    return Path(cm.expand(CFG["state_dir"])) / "ledger.jsonl"


def read_events(since=None, day=None):
    """Gli eventi del ledger nell'intervallo: `day` (data locale) oppure dalle ultime `since` ore."""
    p = ledger_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
            ts = dt.datetime.fromisoformat(e["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if day is not None and ts.date() != day:
            continue
        if since is not None and ts < since:
            continue
        e["_ts"] = ts
        out.append(e)
    return out


def project_name(cwd):
    root = cm.expand(CFG["workspace"]["root"]).rstrip("/")
    if cwd.rstrip("/") == root:
        return CFG["workspace"]["root_session_name"]
    return os.path.basename(cwd.rstrip("/")) or cwd


def summarize(events):
    """Per sessione (nell'ordine del primo evento): progetto, account, inizio, fine, turni, attese, ultimo."""
    sessions = OrderedDict()
    for e in events:
        sid = e.get("session_id") or f"pid-{e.get('pid')}"
        s = sessions.setdefault(sid, {"id": sid, "project": project_name(e.get("cwd", "")), "cwd": e.get("cwd", ""),
                                      "account": e.get("account", ""), "start": None, "end": None, "turns": 0,
                                      "waiting": [], "last": "", "last_ts": None, "alive": True})
        ev = e.get("event")
        t = e["_ts"]
        if ev == "start":
            if s["start"] is None or t < s["start"]:
                s["start"] = t
            s["alive"] = True
            s["end"] = None
        elif ev == "end":
            s["end"] = t
            s["alive"] = False
        elif ev == "stop":
            s["turns"] += 1
            if s["last_ts"] is None or t >= s["last_ts"]:
                s["last"] = (e.get("last") or "").strip()
                s["last_ts"] = t
        elif ev == "waiting":
            s["waiting"].append((t, e.get("tool") or "?"))
    return list(sessions.values())


def hm(t):
    return t.strftime("%H:%M") if t else "?"


def render(sessions, label):
    n_turns = sum(s["turns"] for s in sessions)
    n_wait = sum(len(s["waiting"]) for s in sessions)
    lines = [M("diary.title", label=label, n=len(sessions), turns=n_turns, waits=n_wait)]
    if not sessions:
        lines.append(M("diary.empty"))
        return "\n".join(lines)
    cap = int(D["max_last_chars"])
    for s in sessions:
        span = f"{hm(s['start'])}→{M('diary.alive') if s['alive'] else hm(s['end'])}"
        waits = ""
        if s["waiting"]:
            waits = "  " + M("diary.waits", n=len(s["waiting"]), detail=", ".join(f"{tool} {hm(t)}" for t, tool in s["waiting"][-3:]))
        lines.append(f"{s['account']} · {s['project']:<24} {span:<13} {M('diary.turns', n=s['turns'])}{waits}")
        if s["last"]:
            # la prima riga che dica qualcosa, FUORI dai blocchi di codice (segmenti pari dello
            # split su ```): il codice nel diario e' rumore
            outside = "\n".join(seg for i, seg in enumerate(s["last"].split("```")) if i % 2 == 0)
            first = next((l.strip() for l in outside.splitlines() if l.strip()), "")
            first = first if len(first) <= cap else first[:cap] + "…"
            lines.append(f"   {M('diary.last')}: {first}")
    per_acc = OrderedDict()
    for s in sessions:
        a = per_acc.setdefault(s["account"], [0, 0])
        a[0] += 1
        a[1] += s["turns"]
    lines.append(M("diary.totals", detail=" · ".join(f"{k} {v[0]}/{v[1]}" for k, v in per_acc.items())))
    return "\n".join(lines)


def send(text):
    bot = _load("cm-bot")
    if not bot.token():
        print(M("bot.no_token", path=CFG["bot"]["token_file"]), file=sys.stderr)
        return 1
    chats = bot.allowed_chats()
    if not chats:
        print(M("diary.no_chats"), file=sys.stderr)
        return 1
    for c in sorted(chats):
        bot.reply(c, text)
    print(M("diary.sent", n=len(chats)))
    return 0


# ------------------------------------------------------------------ cron
def cron_line():
    hh, _, mm = str(D["cron_time"]).partition(":")
    shim = cm.home() / ".local" / "bin" / "claude-master"
    return f"{int(mm or 0)} {int(hh or 20)} * * * {shim} diary --send >/dev/null 2>&1"


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def install():
    cur = crontab_read()
    if "claude-master diary --send" in cur:
        print(M("diary.cron_present"))
        return 0
    crontab_write(cur.rstrip("\n") + ("\n" if cur.strip() else "") + "# claude-master diary: il diario della giornata su Telegram\n" + cron_line() + "\n")
    print(M("diary.cron_installed", line=cron_line()))
    return 0


def uninstall():
    cur = crontab_read()
    if "claude-master diary --send" not in cur:
        print(M("diary.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "claude-master diary" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("diary.cron_removed"))
    return 0


def status():
    print(M("diary.status_cron", state="yes" if "claude-master diary --send" in crontab_read() else "no", line=cron_line()))
    p = ledger_path()
    n = sum(1 for _ in p.open()) if p.is_file() else 0
    print(M("diary.status_ledger", path=p, n=n))
    return 0


def main(argv):
    if argv and argv[0] in ("install", "uninstall", "status"):
        return {"install": install, "uninstall": uninstall, "status": status}[argv[0]]()
    ap = argparse.ArgumentParser(prog="claude-master diary", add_help=True)
    ap.add_argument("--date", "--data", dest="date")
    ap.add_argument("--since", "--da", dest="since", type=float, help="ore")
    ap.add_argument("--send", "--invia", dest="send", action="store_true")
    ap.add_argument("--json", dest="as_json", action="store_true")
    a = ap.parse_args(argv)
    if a.since:
        events = read_events(since=dt.datetime.now() - dt.timedelta(hours=a.since))
        label = M("diary.label_since", h=a.since)
    else:
        day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
        events = read_events(day=day)
        label = day.strftime("%d/%m/%Y")
    sessions = summarize(events)
    if a.as_json:
        def ser(s):
            return {**s, "start": s["start"].isoformat() if s["start"] else None, "end": s["end"].isoformat() if s["end"] else None,
                    "last_ts": s["last_ts"].isoformat() if s["last_ts"] else None,
                    "waiting": [(t.isoformat(), tool) for t, tool in s["waiting"]]}
        print(json.dumps([ser(s) for s in sessions], ensure_ascii=False, indent=1))
        return 0
    text = render(sessions, label)
    print(text)
    if a.send:
        return send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

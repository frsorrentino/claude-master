#!/usr/bin/env python3
"""Verifica cm-bot.py con un Telegram finto (HTTP locale), un dispatcher finto e un crontab finto.

B1  spento (bot.enabled false): poll esce 0 senza chiamare l'API
B2  primo giro senza offset: l'arretrato si scarta (niente lanci), offset scritto; B2b un messaggio
    recente (< bot.first_run_max_age_s) nel primo giro viene servito
B3  /master da chat autorizzata: launch <radice> --no-window, risposta col link, offset avanzato
B4  /master da chat NON autorizzata (o gruppo): ignorato, niente lancio, niente risposta
B5  /launch: un candidato → lancia quello; più candidati → elenca e non lancia; nessuno → dice e non crea
B6  /sessions: elenco compatto (resa da polso); /sessions full: la tabella; testo qualsiasi → aiuto
B7  guardia: bot.pid del plugin VIVO → poll non chiama l'API
B8  install (rifiuta se spento; riga nel crontab), status, uninstall
B9  lock: due poll insieme, uno solo lavora
B10 /start (Franz l'ha scritto due volte credendo di lanciare la master, 11/09): risposta «vuoi /master?»
    con un bottone inline; il tap (callback_query, che a sessioni chiuse arriva a QUESTO poller) lancia
    la master, risponde al callback e alla chat; un tap da chat non autorizzata si ignora; getUpdates
    chiede anche i callback_query
B11 sessione GIÀ VIVA (Franz dal polso, 16:30): /master con la tmux «master» viva → nessun launch, risposta «già
    viva» con link e stato; lo stesso per /launch di un progetto già aperto
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": ["1001"], "groups": {}, "pending": {}}))
ws = home / "ws"
for d in ("personali/alfa", "personali/alfabeta", "personali/gamma", "pixelfarm/clienti/sito.com", "pixelfarm/clienti/altro.com", "_archivio/vecchio", ".claude"):
    (ws / d).mkdir(parents=True)
state = tmp / "state"
state.mkdir()

# --- Telegram finto: getUpdates serve una coda scriptata e registra l'offset; sendMessage registra
CALLS = {"getUpdates": [], "sendMessage": []}
QUEUE = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        params = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        method = self.path.rsplit("/", 1)[-1]
        if not self.path.startswith("/bot123:ABC/"):
            self.send_response(401); self.end_headers(); return
        CALLS.setdefault(method, []).append(params)
        if method == "getUpdates":
            off = int(params.get("offset") or 0)
            body = {"ok": True, "result": [u for u in QUEUE if u["update_id"] >= off]}
        elif method == "sendMessage":
            body = {"ok": True, "result": {"message_id": 1}}
        else:
            body = {"ok": False}
        out = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{srv.server_port}"

# --- dispatcher finto: registra gli argomenti, stampa quello che launch/sessions stamperebbero
argslog = tmp / "cm-args.log"
fake_cm = tmp / "claude-master"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  launch) echo "sessione avviata"; echo "  cartella:  $2"; echo "  link:      https://claude.ai/code/session_01BOT" ;;
  sessions)
    if [ "$2" = "--json" ]; then
      if [ -f "{tmp}/alive.json" ]; then cat "{tmp}/alive.json"; else echo "[]"; fi
    else echo "PID ACCOUNT NOME"; echo "1 personale master"; fi ;;
esac
""")
fake_cm.chmod(0o755)
cron = tmp / "crontab"
cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)

cfg = tmp / "config.json"


def write_cfg(enabled=True):
    cfg.write_text(json.dumps({
        "language": "it", "state_dir": str(state),
        "workspace": {"root": str(ws), "excluded_dirs": [".git", "node_modules", ".claude", "_archivio"], "project_dirs": ["personali", "pixelfarm/clienti"]},
        "bot": {"enabled": enabled, "api_base": API, "token_file": str(tg / ".env"), "access_file": str(tg / "access.json"),
                "pid_file": str(tg / "bot.pid"), "cron_minutes": 1},
    }))


def bot(*args):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_BOT_CM": str(fake_cm), "CM_CRONTAB_CMD": str(fake_crontab)}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-bot.py"), *args], capture_output=True, text=True, env=env, timeout=60)


def msg(uid, chat, text, ctype="private"):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat, "type": ctype}, "from": {"id": chat}, "text": text}}


def sent():
    return CALLS["sendMessage"]


def launches():
    return [l for l in argslog.read_text().splitlines() if l.startswith("launch ")] if argslog.exists() else []


offset_file = state / "bot-offset"

# B1
write_cfg(enabled=False)
QUEUE[:] = [msg(1, 1001, "/master")]
r = bot("poll")
T.check("B1 disabled: exit 0, no API call", r.returncode == 0 and not CALLS["getUpdates"], r.stdout + r.stderr)

# B2
write_cfg(enabled=True)
r = bot("poll")
T.check("B2 first run: backlog discarded, offset written, no launch", r.returncode == 0 and offset_file.read_text().strip() == "2" and not launches() and not sent(), r.stdout + r.stderr + (offset_file.read_text() if offset_file.exists() else "-"))

# B2b: primo giro con un /master RECENTE (data di adesso): quello si serve
offset_file.unlink()
import time as _t
recent = msg(2, 1001, "/master"); recent["message"]["date"] = int(_t.time())
QUEUE[:] = [msg(1, 1001, "/master"), recent]
r = bot("poll")
T.check("B2b first run: old /master discarded, the recent one (<180 s) served", r.returncode == 0 and launches() == [f"launch {ws} --no-window"] and offset_file.read_text().strip() == "3", r.stdout + r.stderr + str(launches()))
argslog.write_text(""); CALLS["sendMessage"].clear(); offset_file.write_text("2")   # stato come dopo B2

# B3
QUEUE[:] = [msg(2, 1001, "/master")]
r = bot("poll")
T.check("B3 /master from an allowed chat → launch <root> --no-window", r.returncode == 0 and launches() == [f"launch {ws} --no-window"], r.stdout + r.stderr + str(launches()))
T.check("B3 reply carries the link, offset advanced", sent() and "session_01BOT" in sent()[-1]["text"] and sent()[-1]["chat_id"] == "1001" and offset_file.read_text().strip() == "3", str(sent()))

# B4
before = len(launches()), len(sent())
QUEUE[:] = [msg(3, 2002, "/master"), msg(4, 1001, "/master", ctype="group")]
r = bot("poll")
T.check("B4 unknown chat and group chat ignored: no launch, no reply, offset advanced", r.returncode == 0 and (len(launches()), len(sent())) == before and offset_file.read_text().strip() == "5", r.stdout + r.stderr)
log = (state / "bot.log").read_text()
T.check("B4 ignored updates logged", "ignored update 3" in log and "ignored update 4" in log, log[-400:])

# B5
QUEUE[:] = [msg(5, 1001, "/launch gamma")]
bot("poll")
T.check("B5 unique candidate → launched", launches()[-1] == f"launch {ws / 'personali' / 'gamma'} --no-window", str(launches()))
n_l = len(launches())
QUEUE[:] = [msg(6, 1001, "/launch alfa")]
bot("poll")
T.check("B5 exact name wins over the longer match (alfa vs alfabeta)", launches()[-1] == f"launch {ws / 'personali' / 'alfa'} --no-window" and len(launches()) == n_l + 1, str(launches()[-2:]))
n_l = len(launches())
QUEUE[:] = [msg(7, 1001, "/launch .com")]
bot("poll")
T.check("B5 two candidates → listed, nothing launched", len(launches()) == n_l and "2" in sent()[-1]["text"] and "sito.com" in sent()[-1]["text"] and "altro.com" in sent()[-1]["text"], sent()[-1]["text"])
QUEUE[:] = [msg(8, 1001, "/launch vecchio")]
bot("poll")
T.check("B5 excluded dir never a candidate → «none», nothing created", len(launches()) == n_l and "nessuna cartella" in sent()[-1]["text"] and not (ws / "vecchio").exists(), sent()[-1]["text"])
QUEUE[:] = [msg(9, 1001, "/launch")]
bot("poll")
T.check("B5 /launch without fragment → usage", len(launches()) == n_l and "/launch" in sent()[-1]["text"], sent()[-1]["text"])

# B6
QUEUE[:] = [msg(10, 1001, "/sessions"), msg(11, 1001, "ciao, come va?")]
bot("poll")
T.check("B6 /sessions → the compact list (here empty: no live row); free text at list level → «prima scegli la sessione»", "nessuna sessione" in sent()[-2]["text"] and "prima scegli" in sent()[-1]["text"] and len(launches()) == n_l, str([s["text"][:60] for s in sent()[-2:]]))
QUEUE[:] = [msg(12, 1001, "/sessions full")]
bot("poll")
T.check("B6 /sessions full → the whole table", "personale master" in sent()[-1]["text"], sent()[-1]["text"][:80])
offset_file.write_text("12")   # B7 riusa l'update 12: si torna indietro di uno

# B7
(tg / "bot.pid").write_text(str(os.getpid()))
n_get = len(CALLS["getUpdates"])
QUEUE[:] = [msg(12, 1001, "/master")]
r = bot("poll")
T.check("B7 plugin poller alive (bot.pid) → no API call, no launch", r.returncode == 0 and len(CALLS["getUpdates"]) == n_get and len(launches()) == n_l, r.stdout + r.stderr)
(tg / "bot.pid").write_text("999999")
r = bot("poll")
T.check("B7 dead pid in bot.pid → the bot works again", len(CALLS["getUpdates"]) == n_get + 1 and len(launches()) == n_l + 1, r.stdout + r.stderr)

# B8
write_cfg(enabled=False)
r = bot("install")
T.check("B8 install refuses when disabled", r.returncode != 0 and "bot.enabled" in r.stdout, r.stdout + r.stderr)
write_cfg(enabled=True)
r = bot("install")
T.check("B8 install writes the cron line once", r.returncode == 0 and cron.read_text().count("bot ensure") == 1 and "* * * * *" in cron.read_text(), r.stdout + cron.read_text())
r = bot("install")
T.check("B8 second install: already present", r.returncode == 0 and cron.read_text().count("bot ensure") == 1, r.stdout)
r = bot("status")
T.check("B8 status: enabled, cron yes, token ok, 1 chat, offset", "true" in r.stdout and "token" in r.stdout and "ok" in r.stdout and "1" in r.stdout, r.stdout + r.stderr)
r = bot("uninstall")
T.check("B8 uninstall removes the line", r.returncode == 0 and "bot ensure" not in cron.read_text(), r.stdout + cron.read_text())

# B9: due poll insieme sullo stesso offset → uno solo lavora (lock)
QUEUE[:] = [msg(13, 1001, "/master")]
n_l = len(launches())
env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_BOT_CM": str(fake_cm)}
procs = [subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-bot.py"), "poll"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
for p in procs:
    p.wait(timeout=60)
T.check("B9 two concurrent polls → one launch", len(launches()) == n_l + 1, str(launches()[-2:]))

# B10: /start → bottone; il tap → launch
CALLS["sendMessage"].clear(); CALLS.pop("answerCallbackQuery", None)
n_l = len(launches())
QUEUE[:] = [msg(14, 1001, "/start")]
r = bot("poll")
sm = sent()
T.check("B10 /start: one reply with an inline button whose callback_data is master, no launch", r.returncode == 0 and len(sm) == 1 and "/master" in sm[0]["text"] and '"inline_keyboard"' in sm[0].get("reply_markup", "") and '"callback_data": "master"' in sm[0].get("reply_markup", "") and len(launches()) == n_l, r.stdout + r.stderr + str(sm))
T.check("B10 getUpdates asks for callback_query too", "callback_query" in CALLS["getUpdates"][-1].get("allowed_updates", ""), str(CALLS["getUpdates"][-1]))
CALLS["sendMessage"].clear()
QUEUE[:] = [{"update_id": 15, "callback_query": {"id": "cb15", "data": "master", "from": {"id": 1001},
                                                 "message": {"message_id": 14, "chat": {"id": 1001, "type": "private"}}}}]
r = bot("poll")
T.check("B10 tap from the allowed chat → launch <root>, callback answered, reply in the chat, offset advanced", r.returncode == 0 and len(launches()) == n_l + 1 and CALLS.get("answerCallbackQuery") and CALLS["answerCallbackQuery"][-1].get("callback_query_id") == "cb15" and sent() and "avviata" in sent()[-1]["text"] and offset_file.read_text().strip() == "16", r.stdout + r.stderr + str(CALLS.get("answerCallbackQuery")) + str(sent()))
CALLS["sendMessage"].clear()
QUEUE[:] = [{"update_id": 16, "callback_query": {"id": "cb16", "data": "master", "from": {"id": 4242},
                                                 "message": {"message_id": 14, "chat": {"id": 4242, "type": "private"}}}}]
r = bot("poll")
T.check("B10 tap from a stranger: ignored, no launch, no reply", r.returncode == 0 and len(launches()) == n_l + 1 and not sent() and offset_file.read_text().strip() == "17", r.stdout + r.stderr + str(sent()))

# B11: sessione già viva → niente launch
alive = tmp / "alive.json"
alive.write_text(json.dumps([{"pid": 7, "name": "master", "tmux": "master", "cwd": str(ws), "status": "idle", "link": "https://claude.ai/code/session_01LIVE", "account": "personale"},
                             {"pid": 8, "name": "alfa", "tmux": "alfa", "cwd": str(ws / "personali" / "alfa"), "status": "busy", "link": "https://claude.ai/code/session_01ALFA", "account": "personale"}]))
CALLS["sendMessage"].clear(); n_l = len(launches())
QUEUE[:] = [msg(17, 1001, "/master")]
r = bot("poll")
T.check("B11 /master with the master alive: no launch, reply says already alive with link and status", r.returncode == 0 and len(launches()) == n_l and sent() and "viva" in sent()[-1]["text"] and "session_01LIVE" in sent()[-1]["text"] and "idle" in sent()[-1]["text"], r.stdout + r.stderr + str(sent()) + str(launches()[-1:]))
QUEUE[:] = [{"update_id": 18, "callback_query": {"id": "cb18", "data": "master", "from": {"id": 1001}, "message": {"message_id": 14, "chat": {"id": 1001, "type": "private"}}}}]
r = bot("poll")
T.check("B11 the button tap with the master alive: no launch either", r.returncode == 0 and len(launches()) == n_l and "viva" in sent()[-1]["text"], str(sent()[-1:]) + str(launches()[-1:]))
QUEUE[:] = [msg(19, 1001, "/launch alfa")]
r = bot("poll")
T.check("B11 /launch of an open project: no launch, already alive with its link", r.returncode == 0 and len(launches()) == n_l and "viva" in sent()[-1]["text"] and "session_01ALFA" in sent()[-1]["text"], str(sent()[-1:]) + str(launches()[-1:]))
alive.unlink()
QUEUE[:] = [msg(20, 1001, "/launch alfa")]
r = bot("poll")
T.check("B11 once closed, /launch launches again", len(launches()) == n_l + 1, str(launches()[-1:]))

srv.shutdown()
T.rm(tmp)
T.finish()

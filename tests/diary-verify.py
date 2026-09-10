#!/usr/bin/env python3
"""Verifica cm-diary.py su un ledger finto, con Telegram finto e crontab finto.

DI1 --date: una sessione per riga, progetto, account, inizio→fine o «viva», turni, attese con
    strumento e ora, ultimo messaggio troncato, totali per account
DI2 --json: struttura per sessione
DI3 --since: solo gli eventi recenti
DI4 --send: sendMessage a ogni chat di allowFrom col testo del diario
DI5 install (riga 0 20 * * *), status, uninstall
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
(tg / "access.json").write_text(json.dumps({"allowFrom": ["1001", "1002"]}))
state = tmp / "state"
state.mkdir()
ws = home / "ws"
rows = [
    {"ts": "2026-09-09T09:00:00", "event": "start", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "source": "startup"},
    {"ts": "2026-09-09T09:05:00", "event": "stop", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "last": "prima risposta"},
    {"ts": "2026-09-09T09:10:00", "event": "waiting", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "tool": "AskUserQuestion"},
    {"ts": "2026-09-09T09:20:00", "event": "stop", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "last": "Fatto: tre file modificati e test verdi, " + "x" * 300},
    {"ts": "2026-09-09T10:00:00", "event": "end", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "reason": "other"},
    {"ts": "2026-09-09T11:00:00", "event": "start", "session_id": "B", "cwd": str(ws), "account": "personale", "pid": 2, "source": "startup"},
    {"ts": "2026-09-09T11:30:00", "event": "stop", "session_id": "B", "cwd": str(ws), "account": "personale", "pid": 2, "last": "```\ncodice\n```\nok fatto"},
    {"ts": "2026-09-09T12:00:00", "event": "start", "session_id": "C", "cwd": str(ws / "pixelfarm" / "clienti" / "sito.com"), "account": "professionale", "pid": 3, "source": "startup"},
    {"ts": "2026-09-09T12:30:00", "event": "stop", "session_id": "C", "cwd": str(ws / "pixelfarm" / "clienti" / "sito.com"), "account": "professionale", "pid": 3, "last": "risposta cliente"},
    {"ts": "2026-09-08T12:30:00", "event": "stop", "session_id": "OLD", "cwd": str(ws / "personali" / "vecchia"), "account": "personale", "pid": 4, "last": "ieri"},
]
(state / "ledger.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")

CALLS = {"sendMessage": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        params = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        CALLS.setdefault(self.path.rsplit("/", 1)[-1], []).append(params)
        out = json.dumps({"ok": True, "result": {}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cron = tmp / "crontab"
cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state),
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "diary": {"cron_time": "20:00", "max_last_chars": 40},
}))


def diary(*args):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_CRONTAB_CMD": str(fake_crontab)}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-diary.py"), *args], capture_output=True, text=True, env=env, timeout=60)


r = diary("--date", "2026-09-09")
out = r.stdout
T.check("DI1 title: 3 sessions, 4 turns, 1 wait", r.returncode == 0 and "3 sessioni" in out and "4 turni" in out and "1 " in out, out + r.stderr)
T.check("DI1 alfa: 09:00→10:00, 2 turni, wait with tool and time", "personale · alfa" in out and "09:00→10:00" in out and "2 turni" in out and "AskUserQuestion 09:10" in out, out)
T.check("DI1 root cwd is named master and is still alive; code fence skipped in «last»", "master" in out and "11:00→viva" in out and "ultimo: ok fatto" in out, out)
T.check("DI1 last message: the LATEST stop, truncated at max_last_chars (40)", "ultimo: Fatto: tre file modificati e test verdi,…" in out and "prima risposta" not in out, out)
T.check("DI1 totals per account", "personale 2/3" in out and "professionale 1/1" in out, out)
T.check("DI1 yesterday's session excluded", "vecchia" not in out, out)

r = diary("--date", "2026-09-09", "--json")
try:
    j = json.loads(r.stdout)
    T.check("DI2 --json: 3 sessions with turns/waiting/alive", len(j) == 3 and j[0]["turns"] == 2 and j[0]["alive"] is False and j[1]["alive"] is True and j[0]["waiting"][0][1] == "AskUserQuestion", r.stdout[:300])
except ValueError as e:
    T.check("DI2 --json parses", False, f"{e}: {r.stdout[:200]} {r.stderr[:200]}")

r = diary("--since", "1")
T.check("DI3 --since 1h: nothing from 2026-09-09 (fixture is in the past) → empty diary, exit 0", r.returncode == 0 and "0 sessioni" in r.stdout, r.stdout + r.stderr)

r = diary("--date", "2026-09-09", "--send")
T.check("DI4 --send: one sendMessage per allowed chat with the diary text", r.returncode == 0 and sorted(c["chat_id"] for c in CALLS["sendMessage"]) == ["1001", "1002"] and "alfa" in CALLS["sendMessage"][0]["text"], r.stdout + r.stderr + str(CALLS))

r = diary("install")
T.check("DI5 install: cron line at 20:00 with --send", r.returncode == 0 and "0 20 * * *" in cron.read_text() and "diary --send" in cron.read_text(), r.stdout + cron.read_text())
r = diary("status")
T.check("DI5 status: cron yes, ledger rows", "yes" in r.stdout and "10" in r.stdout, r.stdout + r.stderr)
r = diary("uninstall")
T.check("DI5 uninstall", r.returncode == 0 and "diary" not in cron.read_text(), r.stdout + cron.read_text())

srv.shutdown()
T.rm(tmp)
T.finish()

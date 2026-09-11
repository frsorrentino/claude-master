#!/usr/bin/env python3
"""Verifica cm-recap.py su un ledger finto, con Telegram finto e crontab finto.

DI1 --date: una sessione per riga, progetto, account, inizio→fine o «viva», turni, attese con
    strumento e ora, ultimo messaggio troncato, totali per account
DI2 --json: struttura per sessione
DI3 --since: solo gli eventi recenti
DI4 --send: sendMessage a ogni chat di allowFrom col testo del diario
DI5 install (riga 0 20 * * *), status, uninstall
DI6 recap.min_turns (soglia di sostanza): le chiuse sotto la soglia finiscono in UNA riga «altro: a, b»
    invece di una riga ciascuna; le ferme e le vive restano intere; il conteggio del titolo non cambia
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
for d in ("personali/alfa", "personali/zeta", "pixelfarm/clienti/sito.com"):
    (ws / d).mkdir(parents=True, exist_ok=True)
rows = [
    {"ts": "2026-09-09T09:00:00", "event": "start", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "source": "startup"},
    {"ts": "2026-09-09T09:05:00", "event": "stop", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "last": "prima risposta"},
    {"ts": "2026-09-09T09:10:00", "event": "waiting", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "tool": "AskUserQuestion"},
    {"ts": "2026-09-09T09:20:00", "event": "stop", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "last": "Fatto: tre file modificati e test verdi, " + "x" * 300},
    {"ts": "2026-09-09T10:00:00", "event": "end", "session_id": "A", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 1, "reason": "other"},
    {"ts": "2026-09-09T11:00:00", "event": "start", "session_id": "B", "cwd": str(ws), "account": "personale", "pid": 2, "source": "startup"},
    {"ts": "2026-09-09T11:30:00", "event": "stop", "session_id": "B", "cwd": str(ws), "account": "personale", "pid": 2, "last": "```\ncodice\n```\nok fatto: risposta pronta"},
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
    "accounts": {"personale": {"config_dir": str(home / ".claude"), "shape": "circle"}, "professionale": {"config_dir": str(home / ".claude-pixel"), "shape": "square", "tmux_prefix": "pix-"}},
    "tabs": {"color_registry": str(tmp / "colors")},
    "tile": {"chrome_bridge_cli": ""},
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "recap": {"cron_time": "20:00", "max_last_chars": 40, "summary": "last", "min_turns": 1},
}))


def recap(*args):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_CRONTAB_CMD": str(fake_crontab), "CM_DIARY_FAKE_PROC": "1"}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), *args], capture_output=True, text=True, env=env, timeout=60)


# registro peer finto: B (master) viva e in attesa, con link; A e C chiuse (pid morti)
(home / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
(home / ".claude" / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "B", "status": "waiting", "bridgeSessionId": "session_01LINK", "name": "master"}))
(tmp / "colors").write_text("master\t1\n")   # 🟠 = secondo colore della palette dei tondi
rows.append({"ts": "2026-09-09T11:40:00", "event": "waiting", "session_id": "B", "cwd": str(ws), "account": "personale", "pid": 2, "tool": "AskUserQuestion"})
# seconda sessione di alfa (riavvio) e una chiusa a zero turni
rows += [
    {"ts": "2026-09-09T10:10:00", "event": "start", "session_id": "A2", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 5, "source": "startup"},
    {"ts": "2026-09-09T10:20:00", "event": "stop", "session_id": "A2", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 5, "last": "Gentile cliente, ecco\nriga buona senza email"},
    {"ts": "2026-09-09T10:30:00", "event": "end", "session_id": "A2", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 5, "reason": "other"},
    {"ts": "2026-09-09T13:00:00", "event": "start", "session_id": "Z", "cwd": str(ws / "personali" / "zeta"), "account": "personale", "pid": 6, "source": "startup"},
    {"ts": "2026-09-09T13:01:00", "event": "end", "session_id": "Z", "cwd": str(ws / "personali" / "zeta"), "account": "personale", "pid": 6, "reason": "other"},
]
(state / "ledger.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")

r = recap("--date", "2026-09-09")
out = r.stdout
T.check("DI1 short: title counts shown projects (3, zeta hidden), 1 waiting, no turns", r.returncode == 0 and "3 progetti" in out and "turni" not in out and "1 ferme" in out, out + r.stderr)
T.check("DI1 short: master waiting first, its real icon (🟠 from the colour registry), link in the name, tool and time", out.index("FERME") < out.index("CHIUSE") and "🟠 master (https://claude.ai/code/session_01LINK) · AskUserQuestion" in out and "11:40" not in out, out)
T.check("DI1 short: the waiting one quotes its last message, 'ok fatto:' opener stripped", "     risposta pronta" in out, out)
T.check("DI1 short: closed alfa = one line of what was done (its last message, restarts merged), no icon, no times", "✓ alfa: riga buona senza email" in out and "⚪ alfa" not in out and "09:00" not in out, out)
T.check("DI1 short: zeta (0 turns) hidden, sito.com closed with 1 turno", "zeta" not in out and "✓ sito.\u2060com: risposta cliente" in out, out)
T.check("DI1 short: totals per account with the account's shape, no turns", "⚪ personale 3" in out and "⬜ professionale 1" in out and "3/4" not in out, out)
# DI1b: riassunto di TUTTA la giornata da un modello (finto): JSON {progetto: frase}, con cache per giorno
fake_sum = tmp / "fake-claude-sum.sh"
fake_sum.write_text('#!/bin/sh\necho "$@" >> "%s"\necho \'{"alfa": {"fatto": "Test sistemati e changelog aggiornato", "prossimo": ""}, "sito.com": {"fatto": "Risposta al cliente inviata", "prossimo": "Attendere la conferma del cliente"}, "master": {"fatto": "Coordinamento", "prossimo": "Rispondere alla domanda aperta"}}\'\n' % (tmp / "sum-args.log"))
fake_sum.chmod(0o755)
cfg_model = json.loads(cfg.read_text()); cfg_model["recap"]["summary"] = "model"; cfg.write_text(json.dumps(cfg_model))
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--date", "2026-09-09"], capture_output=True, text=True, env={**{"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_DIARY_FAKE_PROC": "1"}, "CM_CLAUDE_BIN": str(fake_sum)}, timeout=60)
T.check("DI1b model summary: one sentence per project from ALL its stop messages, haiku -p called once", "✓ alfa: Test sistemati e changelog aggiornato" in r.stdout and "✓ sito.\u2060com: Risposta al cliente inviata" in r.stdout and (tmp / "sum-args.log").read_text().count("-p") == 1 and "riga buona senza email" in (tmp / "sum-args.log").read_text(), r.stdout + r.stderr)
T.check("DI1b «prossimo» only where the model gave one: not under closed rows", "prossimo: Attendere" not in r.stdout, r.stdout)
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--date", "2026-09-09"], capture_output=True, text=True, env={**{"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_DIARY_FAKE_PROC": "1"}, "CM_CLAUDE_BIN": str(fake_sum)}, timeout=60)
T.check("DI1b second run served from the day's cache: no new model call", "✓ alfa: Test sistemati e changelog aggiornato" in r.stdout and (tmp / "sum-args.log").read_text().count("-p") == 1, r.stdout)
cfg_model["recap"]["summary"] = "last"; cfg.write_text(json.dumps(cfg_model))
# DI1c: la riga del giorno in docs/recap.md di ogni progetto (dalla cache del modello finto)
logf = ws / "personali" / "alfa" / "docs" / "recap.md"
T.check("DI1c project log written: docs/recap.md with the date line from the model summary", logf.is_file() and "- 2026-09-09: Test sistemati e changelog aggiornato" in logf.read_text() and logf.read_text().startswith("# Recap di alfa"), logf.read_text() if logf.is_file() else "missing")
sito_log = ws / "pixelfarm" / "clienti" / "sito.com" / "docs" / "recap.md"
T.check("DI1c project log carries «prossimo» when the model gave one", sito_log.is_file() and "- 2026-09-09: Risposta al cliente inviata · prossimo: Attendere la conferma del cliente" in sito_log.read_text(), sito_log.read_text() if sito_log.is_file() else "missing")
recap("--date", "2026-09-09")
T.check("DI1c idempotent: a second run leaves ONE line for the date", logf.read_text().count("- 2026-09-09:") == 1, logf.read_text())
T.check("DI1c the root project (master) gets its line too, sito.com as well", (ws / "docs" / "recap.md").is_file() and (ws / "pixelfarm" / "clienti" / "sito.com" / "docs" / "recap.md").is_file(), str(list(ws.rglob("recap.md"))))
# DI6: soglia di sostanza
cfg_thr = json.loads(cfg.read_text()); cfg_thr["recap"]["min_turns"] = 3; cfg.write_text(json.dumps(cfg_thr))
r = recap("--date", "2026-09-09")
out = r.stdout
T.check("DI6 min_turns 3: sito.com (1 turn) folded into one «altro» line, alfa (3 turns) keeps its line", r.returncode == 0 and "✓ sito" not in out and "altro: sito.\u2060com" in out and "✓ alfa: riga buona senza email" in out, out + r.stderr)
T.check("DI6 title still counts 3 projects, master (waiting, 1 turn) untouched", "3 progetti" in out and "🟠 master (https://claude.ai/code/session_01LINK)" in out, out)
cfg_thr["recap"]["min_turns"] = 1; cfg.write_text(json.dumps(cfg_thr))
r = recap("--date", "2026-09-09", "--full")
out = r.stdout
T.check("DI1 --full: one row per session, old format, email/letter lines skipped in «ultimo»", "personale · alfa" in out and "5 sessioni" in out and "ultimo: riga buona senza email" in out and "Gentile" not in out, out)
T.check("DI1 --full: code fence skipped, latest stop quoted, opener stripped, truncated at 40", "ultimo: risposta pronta" in out and "ultimo: tre file modificati e test verdi, xxx" in out and "Fatto:" not in out, out)

r = recap("--date", "2026-09-09", "--json")
try:
    j = json.loads(r.stdout)
    T.check("DI2 --json: 4 projects with turns/alive/link", len(j) == 4 and j[0]["turns"] == 3 and j[0]["alive"] is False and j[1]["alive"] is True and j[1]["link"].endswith("session_01LINK"), r.stdout[:300])
except ValueError as e:
    T.check("DI2 --json parses", False, f"{e}: {r.stdout[:200]} {r.stderr[:200]}")

r = recap("--since", "1")
T.check("DI3 --since 1h: nothing from 2026-09-09 (fixture is in the past) → empty diary, exit 0", r.returncode == 0 and "0 progetti" in r.stdout, r.stdout + r.stderr)

r = recap("--date", "2026-09-09", "--send")
T.check("DI4 --send: one sendMessage per allowed chat, HTML with a link", r.returncode == 0 and sorted(c["chat_id"] for c in CALLS["sendMessage"]) == ["1001", "1002"] and "alfa" in CALLS["sendMessage"][0]["text"] and CALLS["sendMessage"][0].get("parse_mode") == "HTML" and '<a href="https://claude.ai/code/session_01LINK"><b>master</b></a>' in CALLS["sendMessage"][0]["text"], r.stdout + r.stderr + str(CALLS)[:500])

r = recap("install")
T.check("DI5 install: cron line at 20:00 with --send", r.returncode == 0 and "0 20 * * *" in cron.read_text() and "recap --send" in cron.read_text(), r.stdout + cron.read_text())
r = recap("status")
T.check("DI5 status: cron yes, ledger rows", "yes" in r.stdout and "16" in r.stdout, r.stdout + r.stderr)
r = recap("uninstall")
T.check("DI5 uninstall", r.returncode == 0 and "recap" not in cron.read_text(), r.stdout + cron.read_text())

srv.shutdown()
T.rm(tmp)
T.finish()

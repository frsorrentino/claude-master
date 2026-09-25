#!/usr/bin/env python3
"""Verifica cm-night.py con il claude finto (-p), RAM finta, Telegram finto e crontab finto.

NI1 add: voce in coda con account dedotto dalla folder_map, --model/--effort/--max-turns; list; remove; clear
NI2 run --dry-run: dice cosa farebbe, non tocca la coda, niente claude
NI3 run: claude -p nella cartella con --permission-mode e --max-turns, CLAUDE_CONFIG_DIR solo per
    il secondo account, rapporto in docs/notte, voce spostata in done, riassunto --send su Telegram
NI4 guardie: RAM sotto soglia → voce saltata con motivo, resta in coda; --one esegue una sola voce
NI5 install (riga cron alle 02:00), status, uninstall
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
(tg / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
(home / ".claude-pixel").mkdir()
ws = home / "ws"
for d in ("personali/alfa", "agenzia/clienti/sito.com"):
    (ws / d).mkdir(parents=True)
state = tmp / "state"
state.mkdir()
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
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
argslog = tmp / "args.log"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state),
    "workspace": {"root": str(ws)},
    "accounts": {"personale": {"config_dir": "~/.claude"}, "professionale": {"config_dir": "~/.claude-pixel", "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(ws / "agenzia"), "account": "professionale"}],
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "night": {"cron_time": "02:00", "min_free_mb": 500, "max_quota_pct": 80, "max_items_per_run": 3, "max_turns": 12},
}))


def night(*args, free_mb="4000"):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_CRONTAB_CMD": str(fake_crontab), "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_ARGS_LOG": str(argslog),
           "FAKE_CLAUDE_ECHO_ENV": "CLAUDE_CONFIG_DIR", "CM_NIGHT_FREE_MB": free_mb}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night.py"), *args], capture_output=True, text=True, env=env, timeout=120)


queue = state / "night-queue.jsonl"

# NI1
r = night("add", str(ws / "personali" / "alfa"), "sistema i test rossi")
T.check("NI1 add: queued with the default account", r.returncode == 0 and "personale" in r.stdout and queue.is_file(), r.stdout + r.stderr)
r = night("add", str(ws / "agenzia" / "clienti" / "sito.com"), "aggiorna il changelog", "--model", "sonnet", "--effort", "low", "--max-turns", "5")
rows = [json.loads(l) for l in queue.read_text().splitlines()]
T.check("NI1 add: account from folder_map, model/effort/max-turns kept", r.returncode == 0 and rows[1]["account"] == "professionale" and rows[1]["model"] == "sonnet" and rows[1]["effort"] == "low" and rows[1]["max_turns"] == 5 and rows[0]["max_turns"] == 12, r.stdout + r.stderr + str(rows))
r = night("add", str(tmp / "nope"), "x")
T.check("NI1 add: missing folder refused", r.returncode != 0 and len(queue.read_text().splitlines()) == 2, r.stdout + r.stderr)
r = night("list")
T.check("NI1 list shows both", "alfa" in r.stdout and "sito.com" in r.stdout and "2" in r.stdout, r.stdout)
r = night("add", str(ws / "personali" / "alfa"), "da togliere")
rid = [json.loads(l) for l in queue.read_text().splitlines()][-1]["id"]
r = night("remove", rid)
T.check("NI1 remove by id", r.returncode == 0 and rid not in queue.read_text() and len(queue.read_text().splitlines()) == 2, r.stdout + r.stderr)

# NI2
r = night("run", "--dry-run")
T.check("NI2 --dry-run: lists what it would run, queue untouched, claude not called", r.returncode == 0 and r.stdout.count("-p") >= 2 and len(queue.read_text().splitlines()) == 2 and not argslog.exists(), r.stdout + r.stderr)

# NI3
r = night("run", "--send")
args = argslog.read_text().splitlines() if argslog.exists() else []
T.check("NI3 run: claude -p with permission-mode and max-turns, twice", r.returncode == 0 and len(args) == 2 and "-p sistema i test rossi --permission-mode acceptEdits --max-turns 12" in args[0] and "--max-turns 5 --model sonnet --effort low" in args[1], r.stdout + r.stderr + str(args))
T.check("NI3 CLAUDE_CONFIG_DIR only for the second account (T68)", "CLAUDE_CONFIG_DIR=" in args[0] and args[0].rstrip().endswith("CLAUDE_CONFIG_DIR=") and str(home / ".claude-pixel") in args[1], str(args))
reports = sorted((ws / "personali" / "alfa" / "docs" / "notte").glob("*.md")) + sorted((ws / "agenzia" / "clienti" / "sito.com" / "docs" / "notte").glob("*.md"))
T.check("NI3 a report per item in docs/notte with prompt and output", len(reports) == 2 and "sistema i test rossi" in reports[0].read_text() and "Sent to cloud session (fake)" in reports[0].read_text(), str(reports))
T.check("NI3 queue emptied, done has both with rc", queue.read_text().strip() == "" and len((state / "night-done.jsonl").read_text().splitlines()) == 2 and '"rc": 0' in (state / "night-done.jsonl").read_text(), (state / "night-done.jsonl").read_text()[:300])
T.check("NI3 --send: one Telegram message with both items", len(CALLS["sendMessage"]) == 1 and "alfa" in CALLS["sendMessage"][0]["text"] and "sito.com" in CALLS["sendMessage"][0]["text"] and "✓" in CALLS["sendMessage"][0]["text"], str(CALLS["sendMessage"])[:400])

# NI4
argslog.unlink()
night("add", str(ws / "personali" / "alfa"), "uno")
night("add", str(ws / "personali" / "alfa"), "due")
r = night("run", free_mb="100")
T.check("NI4 low RAM: both skipped with the reason, queue intact, claude not called", r.returncode == 0 and "RAM" in r.stdout and len(queue.read_text().splitlines()) == 2 and not argslog.exists(), r.stdout + r.stderr)
r = night("run", "--one")
T.check("NI4 --one runs a single item, one stays queued", r.returncode == 0 and len(argslog.read_text().splitlines()) == 1 and len(queue.read_text().splitlines()) == 1 and "due" in queue.read_text(), r.stdout + queue.read_text())
r = night("clear")
T.check("NI4 clear empties the queue", queue.read_text().strip() == "", r.stdout)

# NI5
r = night("install")
T.check("NI5 install: cron at 02:00 with run --send", r.returncode == 0 and "0 2 * * *" in cron.read_text() and "night run --send" in cron.read_text(), r.stdout + cron.read_text())
r = night("status")
T.check("NI5 status: cron yes, queue 0, done 3", "yes" in r.stdout and "0" in r.stdout and "3" in r.stdout, r.stdout + r.stderr)
r = night("uninstall")
T.check("NI5 uninstall", r.returncode == 0 and "night" not in cron.read_text(), r.stdout + cron.read_text())

srv.shutdown()
T.rm(tmp)
T.finish()

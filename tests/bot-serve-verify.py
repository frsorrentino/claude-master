#!/usr/bin/env python3
"""Verifica il long polling del bot (mandato di Franz 11/09 18:30): `bot serve` persistente, `bot ensure`
dal cron, riconnessione a scalare, lock, status. Telegram finto (lib) con rete «caduta» a comando.

S1  `bot ensure` avvia `serve` (pidfile, processo vivo); un secondo `ensure` non ne avvia un altro; un
    messaggio in coda riceve risposta entro 3 s (il finto tiene la connessione fino a 2 s come Telegram:
    il timeout HTTP del serve deve superare il long poll, http_timeout_s qui e' 1 s — dal vivo era «rete
    assente» a ogni giro)
S2  `poll` con `serve` vivo → salta senza chiamare l'API
S3  rete caduta (503) → il ciclo riconnette a scalare e lo logga; alla ripresa l'update in coda viene
    servito, l'offset non salta (ack solo dopo il dispatch)
S4  un secondo `serve` con il lock preso → esce dicendo perché; `status` mostra serve vivo, pid, ultimo update
S5  kill del serve → `ensure` lo rialza (log di avvio); `uninstall` ferma il daemon e toglie il cron
S6  `install` scrive `bot ensure` nel cron (non più `poll`)
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": ["1001"]}))
ws = home / "ws"; (ws / "personali" / "alfa").mkdir(parents=True)
state = tmp / "state"; state.mkdir()
fail_flag = tmp / "net-down"
os.environ["FAKE_TG_FAIL"] = str(fail_flag)
API, CALLS, QUEUE = T.fake_telegram()
argslog = tmp / "cm-args.log"
fake_cm = tmp / "claude-master"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  sessions) [ "$2" = "--json" ] && echo "[]" || echo "PID ACCOUNT NOME" ;;
  registry) echo '{{"sessioni": []}}' ;;
  quota) echo '{{}}' ;;
esac
""")
fake_cm.chmod(0o755)
cron = tmp / "crontab"; cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state),
    "workspace": {"root": str(ws), "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "bot": {"enabled": True, "api_base": API, "token_file": str(tg / ".env"), "access_file": str(tg / "access.json"),
            "pid_file": str(tg / "bot.pid"), "cron_minutes": 1, "http_timeout_s": 1, "serve_timeout_s": 2},
}))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
       "CM_BOT_CM": str(fake_cm), "CM_CRONTAB_CMD": str(fake_crontab), "FAKE_TG_FAIL": str(fail_flag),
       "CM_BOT_BACKOFF": "0.3 0.3 0.3", "CM_BOT_SERVE_IDLE_S": "0.15"}


def bot(*args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-bot.py"), *args], capture_output=True, text=True, env=ENV, timeout=60)


def serve_pid():
    p = state / "bot-serve.pid"
    try:
        pid = int(p.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def log():
    p = state / "bot.log"
    return p.read_text() if p.is_file() else ""


def msg(uid, text):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": 1001, "type": "private"}, "from": {"id": 1001}, "text": text, "date": int(time.time())}}


(state / "bot-offset").write_text("1")
try:
    # S1
    r = bot("ensure")
    T.check("S1 ensure starts serve: pidfile with a live process, exit 0", r.returncode == 0 and serve_pid() > 0, r.stdout + r.stderr + log())
    pid1 = serve_pid()
    r = bot("ensure")
    T.check("S1 second ensure: same process, nothing new", r.returncode == 0 and serve_pid() == pid1, r.stdout + r.stderr)
    CALLS["sendMessage"].clear()
    QUEUE[:] = [msg(2, "?")]
    t0 = time.time()
    T.check("S1 a queued message is answered within 3 s by the daemon, no «rete assente» in the log", T.wait_until(lambda: CALLS["sendMessage"], 3.5) and time.time() - t0 < 3.5 and "sessioni" in CALLS["sendMessage"][-1]["text"] and "rete assente" not in log(), str(CALLS["sendMessage"]) + log()[-300:])
    T.check("S1 offset acknowledged after dispatch", T.wait_until(lambda: (state / "bot-offset").read_text().strip() == "3", 2), (state / "bot-offset").read_text())
    # S2
    n_get = len(CALLS["getUpdates"])
    QUEUE[:] = []
    r = bot("poll")
    time.sleep(0.3)
    T.check("S2 poll with serve alive: skipped, says so", r.returncode == 0 and "serve" in (r.stdout + r.stderr + log()), r.stdout + r.stderr)
    # S3: rete caduta
    fail_flag.write_text("down")
    time.sleep(2.5)   # la richiesta gia' in attesa (hold di 2 s) finisce prima che l'update entri in coda
    QUEUE[:] = [msg(3, "?")]
    CALLS["sendMessage"].clear()
    T.check("S3 network down: getUpdates fails, the loop logs the reconnection with backoff", T.wait_until(lambda: "riconness" in log() or "reconnect" in log(), 4), log()[-600:])
    time.sleep(0.8)
    T.check("S3 while down, nothing answered and offset untouched", not CALLS["sendMessage"] and (state / "bot-offset").read_text().strip() == "3", str(CALLS["sendMessage"]) + (state / "bot-offset").read_text())
    fail_flag.unlink()
    T.check("S3 network back: the queued update is served, offset advances to 4", T.wait_until(lambda: CALLS["sendMessage"] and (state / "bot-offset").read_text().strip() == "4", 5), str(CALLS["sendMessage"]) + (state / "bot-offset").read_text() + log()[-400:])
    # S4: lock
    r = bot("serve")
    T.check("S4 a second serve refuses: lock held, says why, exit ≠ 0", r.returncode != 0 and ("lock" in (r.stdout + r.stderr).lower() or "già" in (r.stdout + r.stderr) or "already" in (r.stdout + r.stderr)), r.stdout + r.stderr)
    r = bot("status")
    T.check("S4 status shows serve alive with pid and the last update", r.returncode == 0 and str(pid1) in r.stdout and ("ultimo update" in r.stdout or "last update" in r.stdout), r.stdout + r.stderr)
    # S5: kill → ensure rialza; uninstall ferma
    if pid1 > 0:
        os.kill(pid1, signal.SIGTERM)
    T.check("S5 SIGTERM stops the daemon (pidfile cleared or dead)", T.wait_until(lambda: serve_pid() == 0, 3), str(serve_pid()))
    r = bot("ensure")
    T.check("S5 ensure restarts it: new pid, start logged twice", r.returncode == 0 and serve_pid() not in (0, pid1) and log().count("serve: avvio") >= 2 or log().count("serve: start") >= 2, r.stdout + r.stderr + log()[-300:])
    # S6: install scrive ensure
    r = bot("install")
    T.check("S6 install writes «bot ensure» in the cron, not poll", r.returncode == 0 and "bot ensure" in cron.read_text() and "bot poll" not in cron.read_text(), cron.read_text())
    r = bot("uninstall")
    T.check("S5 uninstall removes the cron lines and stops the daemon", r.returncode == 0 and "claude-master bot" not in cron.read_text() and T.wait_until(lambda: serve_pid() == 0, 3), r.stdout + cron.read_text() + str(serve_pid()))
finally:
    pid = serve_pid()
    if pid > 0:
        os.kill(pid, signal.SIGKILL)
T.rm(tmp)
T.finish()

#!/usr/bin/env python3
"""Verifica cm-restore.sh con il claude finto e un tmux privato; init --cron.

X1  --dry-run: elenca le sessioni da rilanciare, salta la viva e la cartella sparita, non lancia
X2  --yes: rilancia in parallelo con --continue e --account, quella di `restore.last` per ultima; log per sessione
X3  registro assente → exit 1; registro vuoto → exit 1
K1  init --cron stampa la riga del crontab con la cadenza di config; --yes la installa (crontab finto via CM_CRONTAB_CMD)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/viva", "ws/pro/beta"):
    (home / d).mkdir(parents=True)
reg = tmp / "registry.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"}],
    "session": {"startup_timeout_s": 20, "death_check_s": 1},
    "terminal": {"backend": "none"},
    "registry": {"file": str(reg), "cron_minutes": 15},
    "restore": {"last": "master", "confirm_timeout_s": 1},
    "tabs": {"color_registry": str(tmp / "colors")},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
argslog = tmp / "args.log"
scen = tmp / "scen"; scen.write_text("plain")


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "CM_PROC_SCAN_PIDS": "",
         "FAKE_CLAUDE_ARGS_LOG": str(argslog), "FAKE_CLAUDE_SCENARIO_FILE": str(scen)}
    e.update(extra)
    return e


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "viva", "bash", "--norc"], env=env(), check=True)
    reg.write_text(json.dumps({"salvato": "2026-09-09T08:00:00+0200", "sessioni": [
        {"nome": "master", "cartella": str(home / "ws"), "account": "personale"},
        {"nome": "alfa", "cartella": str(home / "ws" / "personali" / "alfa"), "account": "personale"},
        {"nome": "viva", "cartella": str(home / "ws" / "personali" / "viva"), "account": "personale"},
        {"nome": "pix-beta", "cartella": str(home / "ws" / "pro" / "beta"), "account": "professionale"},
        {"nome": "sparita", "cartella": str(home / "ws" / "nope"), "account": "personale"},
    ]}))
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh"), "--dry-run"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("X1 dry-run lists 3 to restore, skips the live one and the missing folder", r.returncode == 0 and "(3)" in r.stdout and "viva" in r.stdout and "sparita" in r.stdout
            and "alfa" in r.stdout and "pix-beta" in r.stdout and tm("has-session", "-t", "=alfa").returncode != 0, r.stdout + r.stderr)
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh")], capture_output=True, text=True, env=env(), timeout=60)
    T.check("R2b no terminal and no --yes: lists, relaunches nothing, says how", r.returncode == 0 and "--yes" in r.stdout and not tm("has-session", "-t", "=alfa").returncode == 0, r.stdout + r.stderr)
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh"), "--yes"], capture_output=True, text=True, env=env(), timeout=240)
    T.check("X2 --yes relaunches alfa, pix-beta and master", r.returncode == 0 and all(tm("has-session", "-t", f"={n}").returncode == 0 for n in ("alfa", "pix-beta", "master")), r.stdout + r.stderr + tm("list-sessions").stdout)
    lines = argslog.read_text().splitlines()
    T.check("X2 every relaunch uses -c; master launched last", all(" -c" in f" {l} " or l.startswith("-c") for l in lines[-3:]) and "-n master" in lines[-1], "\n".join(lines[-3:]))
    pane_env = subprocess.run(["bash", "-c", f"tr '\\0' '\\n' < /proc/$(tmux -L {tm.socket} list-panes -t pix-beta -F '#{{pane_pid}}')/environ"], capture_output=True, text=True).stdout
    T.check("X2 professionale relaunched on its account", f"CLAUDE_CONFIG_DIR={home}/.claude-pixel" in pane_env, pane_env[:200])
    T.check("X2 per-session logs", (tmp / "state" / "restore" / "alfa.log").exists() and (tmp / "state" / "restore" / "master.log").exists(), str(list((tmp / "state" / "restore").glob("*"))))
    T.check("X2 outcome lines", r.stdout.count("ok:") >= 3 or r.stdout.count("ok") >= 3, r.stdout)
    # X3
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh"), "--dry-run"], capture_output=True, text=True, env=env(CLAUDE_MASTER_CONFIG=str(cfg)), timeout=60)
    reg.write_text(json.dumps({"salvato": "x", "sessioni": []}))
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh"), "--dry-run"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("X3 empty registry → exit 1", r.returncode == 1, r.stdout + r.stderr)
    reg.unlink()
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh"), "--dry-run"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("X3 missing registry → exit 1", r.returncode == 1, r.stdout + r.stderr)

# K1 init --cron
fakecron = tmp / "crontab-store"
crontab_cmd = tmp / "crontab"
crontab_cmd.write_text(f'#!/bin/bash\nif [ "$1" = -l ]; then cat "{fakecron}" 2>/dev/null; exit 0; fi\ncat > "{fakecron}"\n')
crontab_cmd.chmod(0o755)
r = T.run_config(["init", "--cron"], home, cfg, extra_env={"CM_CRONTAB_CMD": str(crontab_cmd)})
T.check("K1 --cron prints the line with the configured cadence", "*/15 * * * *" in r.stdout and "claude-master registry" in r.stdout and not fakecron.exists(), r.stdout + r.stderr)
r = T.run_config(["init", "--cron", "--yes"], home, cfg, extra_env={"CM_CRONTAB_CMD": str(crontab_cmd)})
T.check("K1 --cron --yes installs it once", fakecron.exists() and fakecron.read_text().count("claude-master registry") == 1, r.stdout + r.stderr + (fakecron.read_text() if fakecron.exists() else ""))
r = T.run_config(["init", "--cron", "--yes"], home, cfg, extra_env={"CM_CRONTAB_CMD": str(crontab_cmd)})
T.check("K1 second --cron --yes does not duplicate", fakecron.read_text().count("claude-master registry") == 1, fakecron.read_text())

T.rm(str(tmp))
T.finish()

#!/usr/bin/env python3
"""Verifica cm-quota.py su file quota finti (formato della statusline di fable-director).

Q1 file per account = quota-<sha256(config_dir)[:8]>.json nella cartella quota.source
Q2 percentuali e reset in tabella; eta' della lettura accanto
Q3 lettura vecchia (> 2 h) marcata; finestra settimanale scaduta dichiarata
Q4 account senza file → «mai visto» + nota; --json con i campi
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = T.tmpdir()
home = Path(tmp) / "home"
src = Path(tmp) / "quota"
src.mkdir()
for d in (".claude", ".claude-pixel"):
    (home / d).mkdir(parents=True)
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "quota": {"source": str(src)},
}))


def qfile(config_dir):
    return src / f"quota-{hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]}.json"


now = time.time()
qfile(home / ".claude").write_text(json.dumps({"five_hour_used_pct": 12.4, "weekly_used_pct": 55.6, "weekly_resets_at": now + 86400}))
f2 = qfile(home / ".claude-pixel")
f2.write_text(json.dumps({"five_hour_used_pct": 90, "weekly_used_pct": 99, "weekly_resets_at": now - 3600}))
os.utime(f2, (now - 3 * 3600, now - 3 * 3600))


def run(*args):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg)}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-quota.py")] + list(args), capture_output=True, text=True, env=env, timeout=30)


r = run()
T.check("Q1 both accounts listed", "personale" in r.stdout and "professionale" in r.stdout, r.stdout + r.stderr)
T.check("Q2 percentages rendered", "12%" in r.stdout and "56%" in r.stdout and "90%" in r.stdout, r.stdout)
T.check("Q2 age shown", "ora" in r.stdout, r.stdout)
T.check("Q3 old reading marked", "vecchia" in r.stdout, r.stdout)
T.check("Q3 expired weekly window declared", "professionale" in r.stdout and "ripartita" in r.stdout, r.stdout)
r = run("--json")
d = json.loads(r.stdout)
T.check("Q4 json fields", d["personale"]["stato"] == "ok" and d["personale"]["settimana_pct"] == 55.6 and d["professionale"]["vecchia"] is True
        and d["professionale"]["finestra_scaduta"] is True, r.stdout[:400])
f2.unlink()
r = run()
T.check("Q4 missing file → mai visto + note", "mai visto" in r.stdout and "statusline" in r.stdout, r.stdout)

T.rm(tmp)
T.finish()

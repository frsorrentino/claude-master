#!/usr/bin/env python3
"""Verifica cm-sessions.py contro un registro finto, processi veri e un tmux privato.

S1  voce del registro con pid vivo e procStart giusto → elencata (account dall'ambiente, T12)
S2  voce stantia (pid morto) → esclusa (T62)
S3  pid riusato (procStart diverso) → esclusa (T62)
S4  processo claude fuori registro → aggiunto dalla scansione di /proc con stato `?` (T11)
S5  VISTA: sessione tmux attaccata (client su pty) → aperta; senza client → STACCATA (T9)
S6  «aspetta una risposta» dallo schermo con DUE indizi (T13); un solo indizio → no
S7  flag dell'hook (state_dir/waiting/<session_id>) vince sullo schermo
S8  CANALE: registro del mio account → nativo; altro account, cartella separata → talk
S9  CANALE: cartella sessions condivisa via symlink → nativo anche per l'altro account (E1)
S10 CANALE: pid antenato di questo processo → (questa)
S11 --json: campi pid, account, name, status, tmux, link, channel
S12 le sessioni ferme e staccate vanno per prime; avviso server tmux morto
"""
import json
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

SCRIPT = T.SCRIPTS / "cm-sessions.py"


def proc_start(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2:].split()[19]


tmp = T.tmpdir()
home = Path(tmp) / "home"
(home / ".claude" / "sessions").mkdir(parents=True)
(home / ".claude-pixel" / "sessions").mkdir(parents=True)
(home / "ws" / "alfa").mkdir(parents=True)
(home / "ws" / "pix" / "beta").mkdir(parents=True)
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "state_dir": str(Path(tmp) / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
}))
procs = []


def spawn(env_conf=None, argv0="sleep"):
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if env_conf:
        env["CLAUDE_CONFIG_DIR"] = env_conf
    p = subprocess.Popen(["bash", "-c", f'exec -a {argv0} sleep 300'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    return p.pid


def entry(reg_dir, pid, name, cwd, tmux_name, status="idle", proc_start_v=None, started=None, sid=None):
    d = {"pid": pid, "name": name, "cwd": cwd, "status": status, "tmux": f"{tmux_name}:@0.%0",
         "startedAt": started or int(time.time() * 1000) - 3600_000, "procStart": proc_start_v or proc_start(pid),
         "sessionId": sid or f"sid-{name}", "bridgeSessionId": f"br_{name}", "messagingSocketPath": f"/tmp/{pid}.sock"}
    Path(reg_dir, f"{pid}.json").write_text(json.dumps(d))
    return d


def run(*args, extra=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_PROC_SCAN_PIDS": " ".join(str(p.pid) for p in procs)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra:
        env.update(extra)
    return subprocess.run([sys.executable, str(SCRIPT)] + list(args), capture_output=True, text=True, env=env, timeout=60)


with T.PrivateTmux() as tm:
    # sessioni tmux finte con una shell dentro
    for s in ("alfa", "pix-beta", "gamma"):
        tm("new-session", "-d", "-s", s, "-x", "100", "-y", "30", "bash", "--norc")
    time.sleep(0.5)
    # client su pty per "alfa": risulta attaccata (T9)
    master, slave = pty.openpty()
    client = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=alfa"], stdin=slave, stdout=slave, stderr=slave,
                              start_new_session=True)
    time.sleep(1)

    # S1: personale, vivo, registro del personale
    p1 = spawn()
    e1 = entry(home / ".claude" / "sessions", p1, "alfa", str(home / "ws" / "alfa"), "alfa", status="busy")
    # S8: professionale in cartella separata
    p2 = spawn(env_conf=str(home / ".claude-pixel"))
    entry(home / ".claude-pixel" / "sessions", p2, "pix-beta", str(home / "ws" / "pix" / "beta"), "pix-beta")
    # S2: stantia
    dead = subprocess.Popen(["sleep", "0.01"]); dead.wait()
    entry(home / ".claude" / "sessions", dead.pid, "morta", str(home), "morta", proc_start_v="1")
    # S3: pid vivo ma procStart sbagliato (pid riusato)
    p3 = spawn()
    entry(home / ".claude" / "sessions", p3, "riusata", str(home), "riusata", proc_start_v="424242")
    # S4: fuori registro, cmdline "claude"
    p4 = spawn(argv0="claude")
    # S10: questo processo di test come antenato
    entry(home / ".claude" / "sessions", os.getpid(), "questa-prova", str(home), "gamma")

    r = run("--json")
    T.check("S run exit 0", r.returncode == 0, r.stderr[-500:])
    rows = json.loads(r.stdout) if r.returncode == 0 else []
    by = {x["name"] or f"pid{x['pid']}": x for x in rows}
    T.check("S1 live entry listed with status busy", by.get("alfa", {}).get("status") == "busy", str(list(by)))
    T.check("S1 account from environment (personale)", by.get("alfa", {}).get("account") == "personale", str(by.get("alfa")))
    T.check("S1 link from bridgeSessionId", by.get("alfa", {}).get("link") == "https://claude.ai/code/session_br_alfa", str(by.get("alfa")))
    T.check("S2 stale entry excluded", "morta" not in by, str(list(by)))
    T.check("S3 reused pid excluded", "riusata" not in by, str(list(by)))
    T.check("S4 unregistered claude process from /proc with status ?", by.get(f"pid{p4}", {}).get("status") == "?", str(list(by)))
    T.check("S5 alfa attached (client on pty)", by.get("alfa", {}).get("attached") is True, str(by.get("alfa")))
    T.check("S5 pix-beta detached", by.get("pix-beta", {}).get("attached") is False, str(by.get("pix-beta")))
    T.check("S8 professionale in separate dir → talk", by.get("pix-beta", {}).get("channel") == "talk", str(by.get("pix-beta")))
    T.check("S8 personale → nativo", by.get("alfa", {}).get("channel") == "nativo", str(by.get("alfa")))
    T.check("S10 ancestor pid → (questa)", by.get("questa-prova", {}).get("channel") == "(questa)", str(by.get("questa-prova")))
    T.check("S11 json fields", all(k in by.get("alfa", {}) for k in ("pid", "account", "name", "status", "tmux", "link", "channel", "waiting")), str(by.get("alfa")))

    # S6: schermo con due indizi in pix-beta
    tm("send-keys", "-t", "pix-beta", "printf '\\n  1. Yes\\n  2. No\\n  Enter to select · Esc to cancel\\n'", "Enter")
    time.sleep(0.8)
    rows = json.loads(run("--json").stdout)
    by = {x["name"] or f"pid{x['pid']}": x for x in rows}
    T.check("S6 numbered list + 'to select' → waiting", by.get("pix-beta", {}).get("waiting") is True,
            "pane: " + repr(tm("capture-pane", "-p", "-t", "pix-beta").stdout[-300:]))
    T.check("S12 waiting+detached sorted first", rows and rows[0]["name"] == "pix-beta", str([x["name"] for x in rows]))
    # un solo indizio (solo "Esc to cancel", niente elenco) → no
    tm("send-keys", "-t", "gamma", "clear; printf '\\n  working...  Esc to cancel\\n'", "Enter")
    time.sleep(0.8)
    rows = json.loads(run("--json").stdout)
    by = {x["name"] or f"pid{x['pid']}": x for x in rows}
    T.check("S6 single hint → not waiting", by.get("questa-prova", {}).get("waiting") is False, str(by.get("questa-prova")))

    # S6-bis: stato `waiting` nel registro → aspetta (senza guardare lo schermo)
    e = json.loads((home / ".claude" / "sessions" / f"{p1}.json").read_text()); e["status"] = "waiting"
    (home / ".claude" / "sessions" / f"{p1}.json").write_text(json.dumps(e))
    rows = json.loads(run("--json", "--no-screen").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S6-bis registry status waiting → waiting", by.get("alfa", {}).get("waiting") is True, str(by.get("alfa")))
    e["status"] = "busy"; (home / ".claude" / "sessions" / f"{p1}.json").write_text(json.dumps(e))

    # S7: flag dell'hook vince
    (Path(tmp) / "state" / "waiting").mkdir(parents=True)
    (Path(tmp) / "state" / "waiting" / "sid-alfa").write_text("AskUserQuestion")
    rows = json.loads(run("--json").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S7 hook flag → waiting even with clean screen", by.get("alfa", {}).get("waiting") is True, str(by.get("alfa")))

    # tabella
    r = run()
    T.check("S table renders header and rows", "PID" in r.stdout and "alfa" in r.stdout and "aperta" in r.stdout, r.stdout[:500])
    T.check("S table abandoned note", "nessuno la guarda" in r.stdout or "ferma" in r.stdout, r.stdout)

    # S9: cartella condivisa via symlink
    import shutil
    shutil.rmtree(home / ".claude-pixel" / "sessions")
    os.symlink(home / ".claude" / "sessions", home / ".claude-pixel" / "sessions")
    entry(home / ".claude" / "sessions", p2, "pix-beta", str(home / "ws" / "pix" / "beta"), "pix-beta")
    rows = json.loads(run("--json").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S9 shared registry → professionale reachable natively", by.get("pix-beta", {}).get("channel") == "nativo", str(by.get("pix-beta")))
    T.check("S9 account still from environment", by.get("pix-beta", {}).get("account") == "professionale", str(by.get("pix-beta")))
    T.check("S9 shared dir read once (no duplicate rows)", sum(1 for x in rows if x["name"] == "pix-beta") == 1, str([x["name"] for x in rows]))

    client.kill()

# S12: server tmux morto → avviso
r = run("--json", extra={"CM_TMUX_ARGS": "-L cm-nonexistent-socket"})
T.check("S12 no tmux server: rows still listed from registry", r.returncode == 0 and "alfa" in r.stdout, r.stderr[-300:])
r = run(extra={"CM_TMUX_ARGS": "-L cm-nonexistent-socket"})
T.check("S12 tmux dead warning", "tmux" in r.stdout and ("non risponde" in r.stdout or "not responding" in r.stdout), r.stdout[-400:])

for p in procs:
    p.kill()
T.rm(tmp)
T.finish()

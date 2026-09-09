#!/usr/bin/env python3
"""Verifica cm-registry.sh e cm-close.sh su un tmux privato con il claude finto.

R1  registry scrive nome/cartella/account delle sessioni tmux vive (formato legacy)
R2  guardia T19: senza sessioni NON sovrascrive; --show stampa
C1  close NOME chiude (=NOME esatto: `alfa` non tocca `alfa-2`, T1/T50)
C2  close su sessione attaccata → exit 4 e resta viva (T14)
C3  close inesistente → exit 3 con l'elenco
C4  --abandoned --dry-run elenca solo le staccate ferme su una domanda; senza --dry-run le chiude
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

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", "ws/alfa", "ws/alfa-2", "ws/gamma"):
    (home / d).mkdir(parents=True)
reg = tmp / "registry.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "registry": {"file": str(reg)},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CLAUDE_CONFIG_DIR": str(home / ".claude")}
    e.update(extra)
    return e


def run(script, *args):
    return subprocess.run([str(T.SCRIPTS / script)] + list(args), capture_output=True, text=True, env=env(), timeout=60)


with T.PrivateTmux() as tm:
    # tre sessioni con il claude finto dentro (si registra da solo nel registro peer)
    for name, scen in (("alfa", "plain"), ("alfa-2", "plain"), ("gamma", "question")):
        tm("new-session", "-d", "-s", name, "-x", "100", "-y", "30", "-c", str(home / "ws" / name),
           f"env CLAUDE_CONFIG_DIR='{home}/.claude' FAKE_CLAUDE_SCENARIO={scen} '{FAKE}' --dangerously-skip-permissions -n {name}")
    time.sleep(2)
    r = run("cm-registry.sh")
    d = json.loads(reg.read_text())
    names = {s["nome"]: s for s in d["sessioni"]}
    T.check("R1 registry has the three sessions", set(names) == {"alfa", "alfa-2", "gamma"}, str(names))
    T.check("R1 legacy fields nome/cartella/account", names["alfa"]["cartella"] == str(home / "ws" / "alfa") and names["alfa"]["account"] == "personale", str(names["alfa"]))
    r = run("cm-registry.sh", "--show")
    T.check("R2 --show prints the file", '"alfa"' in r.stdout, r.stdout)

    # C2: alfa attaccata via pty
    master, slave = pty.openpty()
    client = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=alfa"], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    time.sleep(1)
    r = run("cm-close.sh", "alfa")
    T.check("C2 attached session refused (exit 4), still alive", r.returncode == 4 and tm("has-session", "-t", "=alfa").returncode == 0, r.stderr)
    r = run("cm-close.sh", "nessuna")
    T.check("C3 missing → exit 3 with the list", r.returncode == 3 and "alfa" in r.stderr, r.stderr)
    # C4: gamma e' staccata e ferma sul menu numerato del claude finto
    r = run("cm-close.sh", "--abandoned", "--dry-run")
    T.check("C4 --abandoned --dry-run lists only gamma", "gamma" in r.stdout and "alfa" not in r.stdout, r.stdout + r.stderr)
    T.check("C4 dry run closes nothing", tm("has-session", "-t", "=gamma").returncode == 0, tm("list-sessions").stdout)
    r = run("cm-close.sh", "--abandoned")
    time.sleep(0.5)
    T.check("C4 --abandoned closes gamma, keeps alfa and alfa-2",
            tm("has-session", "-t", "=gamma").returncode != 0 and tm("has-session", "-t", "=alfa-2").returncode == 0, tm("list-sessions").stdout)
    client.kill()
    time.sleep(0.5)
    # C1: chiudere "alfa" non deve toccare "alfa-2" (prefisso!)
    tm("kill-session", "-t", "=alfa")   # alfa via, resta alfa-2: senza `=` un kill di "alfa" prenderebbe alfa-2
    tm("new-session", "-d", "-s", "alfa", "bash", "--norc")
    r = run("cm-close.sh", "alfa")
    T.check("C1 close alfa closes alfa only", r.returncode == 0 and tm("has-session", "-t", "=alfa").returncode != 0 and tm("has-session", "-t", "=alfa-2").returncode == 0,
            r.stdout + tm("list-sessions").stdout)
    tm("kill-session", "-t", "=alfa")   # se per errore fosse rimasta
    r = run("cm-close.sh", "alfa")      # ora "alfa" non esiste: NON deve agganciare alfa-2 per prefisso
    T.check("C1 close of a missing exact name never hits the -2 sibling (T50)", r.returncode == 3 and tm("has-session", "-t", "=alfa-2").returncode == 0, r.stderr + tm("list-sessions").stdout)

    # R2: guardia sul vuoto
    tm("kill-server")
    before = reg.read_text()
    r = run("cm-registry.sh")
    T.check("R2 no sessions → registry untouched (T19)", reg.read_text() == before, reg.read_text())

T.rm(str(tmp))
T.finish()

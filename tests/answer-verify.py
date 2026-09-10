#!/usr/bin/env python3
"""Verifica cm-answer.py col claude finto (scenario question2: due domande di fila) su un tmux privato.

A1  --show: domanda e opzioni numerate, cursore sulla 1
A2  answer 3: Giù x2 + Invio → «→ verde», compare la seconda domanda; answer 2 sulla seconda → «→ M»
A3  senza domanda aperta: --show e answer rifiutano senza toccare tasti; opzione inesistente rifiutata
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text('{"language": "it", "state_dir": "%s"}' % (tmp / "state"))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(tm):
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}


def answer(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-answer.py"), *args], capture_output=True, text=True, env=env(tm), timeout=60)


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "-x", "120", "-y", "40",
                    "env", "FAKE_CLAUDE_SCENARIO=question2", "FAKE_CLAUDE_REGISTER=0", str(FAKE)], env=env(tm), check=True)
    time.sleep(2)
    r = answer(tm, "alfa", "--show")
    T.check("A1 --show: question, four numbered options, cursor on 1", r.returncode == 0 and "colore preferito?" in r.stdout and "❯ 1. rosso" in r.stdout and "  4. Type something." in r.stdout, r.stdout + r.stderr)
    r = answer(tm, "alfa", "3")
    scr = subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-t", "alfa"], capture_output=True, text=True).stdout
    T.check("A2 answer 3 → verde chosen, second question on screen", r.returncode == 0 and "risposto 3. verde" in r.stdout and "taglia?" in scr, r.stdout + r.stderr + scr)
    T.check("A2 the leftover question is shown after answering", "un'altra domanda" in r.stdout and "❯ 1. S" in r.stdout, r.stdout)
    r = answer(tm, "alfa", "2")
    scr = subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-t", "alfa"], capture_output=True, text=True).stdout
    T.check("A2 answer 2 on the second question → M, no more questions", r.returncode == 0 and "risposto 2. M" in r.stdout and "→ M" in scr and "Enter to select" not in scr, r.stdout + scr)
    r = answer(tm, "alfa", "--show")
    T.check("A3 no open question: --show says so, exit 1", r.returncode == 1 and "nessuna domanda" in r.stdout, r.stdout + r.stderr)
    r = answer(tm, "alfa", "1")
    T.check("A3 no open question: answer refuses, no keys sent", r.returncode == 1 and "nessuna domanda" in r.stdout, r.stdout + r.stderr)
    r = answer(tm, "nessuna", "1")
    T.check("A3 unknown session refused", r.returncode == 1 and "nessuna sessione" in r.stderr, r.stdout + r.stderr)
T.rm(tmp)
T.finish()

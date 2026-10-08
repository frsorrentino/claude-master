#!/usr/bin/env python3
"""supervisor finta per plan-verify: sessions/launch/talk/wait/close su un file di stato (FAKE_CM_DIR).

Il comportamento delle sessioni sta in FAKE_CM_DIR/script.json: {cartella: [azioni per talk]}, dove ogni azione
e' {"touch": [file], "say": "testo della risposta"}; oltre la lista si ripete l'ultima. Ogni chiamata va in
calls.log (una riga JSON), cosi' il test vede ordine, brief e chiusure."""
import json
import os
import sys
import time
from pathlib import Path

D = Path(os.environ["FAKE_CM_DIR"])
STATE = D / "sessions.json"


def rows():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return []


def save(r):
    STATE.write_text(json.dumps(r))


def log(args):
    with open(D / "calls.log", "a") as f:
        f.write(json.dumps({"t": time.time(), "args": args}) + "\n")


args = sys.argv[1:]
log(args)
cmd = args[0]
if cmd == "sessions":
    print(json.dumps(rows()))
elif cmd == "launch":
    proj = args[1]
    r = rows()
    r.append({"tmux": Path(proj).name, "name": Path(proj).name, "cwd": proj, "account": "personal"})
    save(r)
    print(f"launched {Path(proj).name}")
elif cmd == "talk":
    name, text = args[1], args[2]
    if name == "master":
        sys.exit(0)
    row = next((x for x in rows() if x["tmux"] == name), None)
    if not row:
        print(f"no session {name}")
        sys.exit(3)
    script = json.loads((D / "script.json").read_text()).get(row["cwd"], [{}])
    n = sum(1 for line in (D / "calls.log").read_text().splitlines() if json.loads(line)["args"][:2] == ["talk", name])
    act = script[min(n, len(script)) - 1]
    time.sleep(float(act.get("sleep", 0.3)))
    for f in act.get("touch", []):
        (Path(row["cwd"]) / f).write_text("x")
    (D / f"reply-{name}.txt").write_text(act.get("say", "done"))
    print("delivered")
elif cmd == "wait":
    name = args[1]
    p = D / f"reply-{name}.txt"
    print(p.read_text() if p.exists() else "")
elif cmd == "close":
    save([x for x in rows() if x["tmux"] != args[1]])
    print(f"closed {args[1]}")
else:
    print(f"fake: unknown {cmd}")
    sys.exit(2)

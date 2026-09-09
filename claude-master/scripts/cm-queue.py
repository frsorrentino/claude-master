#!/usr/bin/env python3
"""claude-master queue — accoda un prompt a una sessione; lo riceve al prossimo Stop (E5, 2.5).

  claude-master queue <nome> "prompt" [--expires <minuti>]   accoda (scadenza default 12 h)
  claude-master queue <nome> --show                          mostra la coda
  claude-master queue <nome> --clear                         svuota

Lo Stop hook della sessione consegna UNA voce per turno con {"decision":"block","reason":...},
mai quando `stop_hook_active` e' vero: la coda si svuota, non gira in tondo. Ogni voce
scade: un lavoro accodato ieri non deve partire domani a sorpresa.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)


def main(argv):
    if not argv:
        print(cm.msg(CFG, "queue.usage"), file=sys.stderr)
        return 2
    name = argv[0]
    row = next((r for r in sessions.collect(read_screen=False) if r["name"] == name or r["tmux"] == name), None)
    if not row or not row.get("session_id"):
        print(cm.msg(CFG, "talk.missing", name=name), file=sys.stderr)
        return 3
    q = Path(cm.expand(CFG["state_dir"])) / "queue" / row["session_id"]
    if "--show" in argv:
        print(q.read_text() if q.is_file() else cm.msg(CFG, "queue.empty", name=name))
        return 0
    if "--clear" in argv:
        q.unlink(missing_ok=True)
        print(cm.msg(CFG, "queue.cleared", name=name))
        return 0
    text = argv[1] if len(argv) > 1 else ""
    if not text or text.startswith("--"):
        print(cm.msg(CFG, "queue.usage"), file=sys.stderr)
        return 2
    minutes = int(argv[argv.index("--expires") + 1]) if "--expires" in argv else 12 * 60
    q.parent.mkdir(parents=True, exist_ok=True)
    with open(q, "a") as f:
        f.write(json.dumps({"text": text, "queued": time.strftime("%Y-%m-%dT%H:%M:%S"), "expires": time.time() + minutes * 60},
                           ensure_ascii=False) + "\n")
    n = sum(1 for l in q.read_text().splitlines() if l.strip())
    print(cm.msg(CFG, "queue.added", name=name, n=n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""claude-master answer — rispondere alla domanda (AskUserQuestion, permesso) di un'altra sessione.

  claude-master answer <nome> --show              la domanda aperta e le sue opzioni numerate
  claude-master answer <nome> <n> [<n> ...] [--text "..."]
                                                   sceglie l'opzione n (piu' numeri = piu' domande di fila);
                                                   --text per l'opzione «Type something.»

Il dialogo di Claude Code e' un menu nel terminale: si legge lo schermo della sessione tmux
(capture-pane, nome nudo: T1), si conta dove sta il cursore «❯», si manda Su/Giu' fino
all'opzione voluta, si verifica sullo schermo e si conferma con Invio: e' la stessa strada di
`launch` per i dialoghi di fiducia (T3). Provato dal vivo il 10/09/2026: «Down, Enter» → «→ Blu»
e la sessione riparte. Un messaggio nell'inbox NON risponde a un dialogo aperto.

Senza «Enter to select» sullo schermo non c'e' nessuna domanda: esce senza toccare tasti.
E' il pezzo che manca al telefono: dalla master, «rispondi 2 a progetto-x» → `claude-master answer progetto-x 2`.
"""
import importlib.util
import re
import subprocess
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
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
OPTION = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*\S)\s*$")
FOOTER = "Enter to select"


def tmux(*args):
    return subprocess.run(sessions.TMUX + list(args), capture_output=True, text=True)


def screen(name):
    return tmux("capture-pane", "-p", "-t", name).stdout


def parse(text):
    """(header, domanda, [(n, label, corrente)], footer presente). Le opzioni sono le righe
    «n. label»; la descrizione sotto (indentata, senza numero) non si conta."""
    lines = text.splitlines()
    if not any(FOOTER in l for l in lines):
        return None
    options, header, question = [], "", ""
    for i, l in enumerate(lines):
        m = OPTION.match(l)
        if m:
            options.append((int(m.group(2)), m.group(3).strip(), m.group(1) == "❯"))
            continue
        if "☐" in l or "☑" in l:
            header = l.replace("☐", "").replace("☑", "").strip()
            question = next((x.strip() for x in lines[i + 1:i + 3] if x.strip() and not OPTION.match(x)), "")
    return header, question, options, True


def show(name):
    d = parse(screen(name))
    if not d:
        print(M("answer.no_question", name=name))
        return 1
    header, question, options, _ = d
    print(M("answer.question", name=name, header=header or "-", question=question or "-"))
    for n, label, cur in options:
        print(f"  {'❯' if cur else ' '} {n}. {label}")
    return 0


def choose(name, n, text):
    d = parse(screen(name))
    if not d:
        print(M("answer.no_question", name=name))
        return 1
    _, question, options, _ = d
    numbers = [o[0] for o in options]
    if n not in numbers:
        print(M("answer.no_option", n=n, options=", ".join(str(x) for x in numbers)))
        return 2
    current = next((o[0] for o in options if o[2]), numbers[0])
    step = "Down" if n > current else "Up"
    for _ in range(abs(n - current)):
        tmux("send-keys", "-t", name, step)
        time.sleep(0.15)
    time.sleep(0.3)
    d2 = parse(screen(name))
    if not d2 or not any(o[0] == n and o[2] for o in d2[2]):
        print(M("answer.cursor_lost", n=n))
        return 3
    label = next(o[1] for o in d2[2] if o[0] == n)
    tmux("send-keys", "-t", name, "Enter")
    if text and re.search(r"(?i)type something", label):
        time.sleep(0.5)
        tmux("send-keys", "-t", name, "-l", text)
        tmux("send-keys", "-t", name, "Enter")
    time.sleep(1.0)
    print(M("answer.chosen", name=name, n=n, label=label, question=question or "-"))
    return 0


def main(argv):
    if len(argv) < 2:
        print(M("answer.usage"), file=sys.stderr)
        return 2
    name = argv[0]
    if tmux("has-session", "-t", f"={name}").returncode != 0:
        print(M("answer.no_session", name=name), file=sys.stderr)
        return 1
    if argv[1] == "--show":
        return show(name)
    text = ""
    picks = []
    rest = argv[1:]
    i = 0
    while i < len(rest):
        if rest[i] == "--text" and i + 1 < len(rest):
            text = rest[i + 1]; i += 2
        elif rest[i].isdigit():
            picks.append(int(rest[i])); i += 1
        else:
            print(M("answer.usage"), file=sys.stderr)
            return 2
    for n in picks:
        rc = choose(name, n, text)
        if rc:
            return rc
        time.sleep(0.8)   # una domanda successiva ha bisogno di un attimo per comparire
    left = parse(screen(name))
    if left:
        print(M("answer.more", name=name))
        return show(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

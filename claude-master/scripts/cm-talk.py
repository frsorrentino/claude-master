#!/usr/bin/env python3
"""claude-master talk — manda un prompt a un'altra sessione Claude e restituisce la sua risposta.
claude-master wait — aspetta che una sessione torni ferma e stampa la sua ultima risposta.

  claude-master talk <nome> "prompt" [--wait S] [--quiet S] [--no-wait] [--force]
                                     [--via auto|socket|tmux] [--no-native-hint]
  claude-master wait <nome> [--timeout S]

Due vie, scelte dai dati del registro peer (1.6, E6):

- **socket** (dal 09/09/2026, E6a/E6b): il registro `sessions/<pid>.json` dichiara
  il socket inbox della sessione e il file `.key` accanto porta il `peerToken`.
  Si consegna il messaggio nel formato catturato dal vivo (riga di auth + riga
  del messaggio) e si legge la risposta dal TRANSCRIPT (`projects/<slug>/<id>.jsonl`),
  non dallo schermo: niente righe spezzate, niente testo scorso via. Vale per
  qualunque account: il socket e' un file dello stesso utente.
- **tmux** (ripiego, = parla-con.sh): si digita nel riquadro e si legge lo
  schermo. Prima si controlla che nella casella non ci sia gia' testo DIGITATO
  (T16): il suggerimento grigio di Claude Code si riconosce da SGR 2 (`\\e[2m`),
  a volte non chiuso da `\\e[0m`; va letta l'ULTIMA riga con ❯; dopo ❯ c'e' uno
  spazio unificatore U+00A0. Escape, poi il testo con -l, poi Invio separato (T17).
  La fine si indovina dallo schermo fermo, con un tetto.

Chi legge da una sessione Claude dello stesso registro ha una via migliore:
`SendMessage` con `notify_when_idle` (T45). Lo script lo dice (talk.warn_native_channel)
e procede: non puo' chiamare un tool di sessione al posto tuo.
"""
import glob
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
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


def die(key, code, **kw):
    print(M(key, **kw), file=sys.stderr)
    sys.exit(code)


def find(name):
    rows = sessions.collect(read_screen=False)
    for r in rows:
        if r["name"] == name or r["tmux"] == name:
            return r
    return None


def transcript_path(row):
    """<config_dir>/projects/<slug>/<sessionId>.jsonl — slug: ogni non alfanumerico → '-'."""
    acc = CFG["accounts"].get(row["account"]) or {}
    conf = cm.expand(acc.get("config_dir", "~/.claude"))
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(row["cwd"]))
    return Path(conf) / "projects" / slug / f"{row['session_id']}.jsonl"


def assistant_texts(path, after_offset):
    """Testi delle risposte assistant scritte dopo `after_offset` byte; ritorna (testi, nuovo offset)."""
    texts = []
    try:
        with open(path, "rb") as f:
            f.seek(after_offset)
            data = f.read()
    except OSError:
        return texts, after_offset
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        for block in (d.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
    return texts, after_offset + len(data)


def status_of(row):
    try:
        d = json.load(open(Path(row["registry"]) / f"{row['pid']}.json"))
        return d.get("status") or "?"
    except (OSError, ValueError):
        return "gone"


# ------------------------------------------------------------------ socket
def post_socket(row, text, from_name, from_mode):
    """E6b: auth con il peerToken del destinatario, poi il messaggio."""
    keys = glob.glob(str(Path(row["registry"]) / f"{row['pid']}.*.key"))
    token = ""
    if keys:
        try:
            token = json.load(open(keys[0])).get("peerToken", "")
        except (OSError, ValueError):
            pass
    own = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
    content = (f'<cross-session-message from="uds:{own}" from-name="{from_name}" from-mode="{from_mode}">\n'
               f"{text}\n</cross-session-message>")
    msg = {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "user",
           "message": {"role": "user", "content": content}, "priority": "next", "from": f"uds:{own}"}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(row["socket"])
    s.sendall((json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps(msg) + "\n").encode())
    s.close()


def wait_reply(row, offset, max_wait, quiet):
    """Legge il transcript finche' la sessione torna idle (registro) o lo schermo del
    transcript resta fermo per `quiet` secondi; tetto `max_wait`."""
    start = time.time()
    texts = []
    last_change = time.time()
    seen_busy = False
    while True:
        new, offset = assistant_texts(row["_transcript"], offset)
        if new:
            texts += new
            last_change = time.time()
        st = status_of(row)
        if st == "busy":
            seen_busy = True
        if seen_busy and st == "idle" and time.time() - last_change > 1.5:
            break
        if st == "gone":
            break
        if time.time() - last_change > quiet and texts:
            break
        if time.time() - start > max_wait:
            print(M("talk.timeout", s=max_wait))
            break
        time.sleep(1)
    return texts


# ------------------------------------------------------------------ tmux
def tmux(*args):
    return sessions.tmux(*args)


def typed_text(tm):
    """T16: cio' che c'e' nella casella ESCLUSI i suggerimenti (SGR 2)."""
    raw = subprocess.run(sessions.TMUX + ["capture-pane", "-p", "-e", "-t", tm], capture_output=True, text=True).stdout
    lines = [l for l in raw.splitlines() if "❯" in l]
    if not lines:
        return ""
    line = lines[-1]
    line = re.sub(r"\x1b\[2m.*?\x1b\[0m", "", line)   # suggerimento chiuso
    line = re.sub(r"\x1b\[2m.*$", "", line)           # suggerimento fino a fine riga
    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)   # altri attributi
    line = re.sub(r"^\s*❯[\s ]*", "", line)
    return re.sub(r"[\s ]*$", "", line)


def talk_tmux(row, text, force, max_wait, quiet, no_wait):
    tm = row["tmux"]
    if not tm:
        die("talk.no_tmux", 3, name=row["name"])
    residue = typed_text(tm)
    if residue and not force:
        print(M("talk.typed_text", name=tm, text=residue), file=sys.stderr)
        sys.exit(4)
    before = tmux("capture-pane", "-p", "-t", tm) or ""
    # Escape svuota il campo (T17) — solo quando c'e' un residuo da buttare (--force):
    # in Claude Code Esc a turno in corso INTERROMPE il turno, meglio non mandarlo a vuoto
    if residue:
        tmux("send-keys", "-t", tm, "Escape")
        time.sleep(0.6)   # oltre l'escape-time di tmux (500 ms): ESC + testo non deve diventare Meta+lettera
    tmux("send-keys", "-t", tm, "-l", text)
    time.sleep(0.4)
    tmux("send-keys", "-t", tm, "Enter")
    if no_wait:
        return []
    start = time.time()
    still = 0
    last = None
    while True:
        time.sleep(2)
        now = tmux("capture-pane", "-p", "-t", tm) or ""
        if now == last:
            still += 2
            if still >= quiet:
                break
        else:
            still, last = 0, now
        if time.time() - start >= max_wait:
            print(M("talk.timeout", s=max_wait))
            break
    after = tmux("capture-pane", "-p", "-t", tm) or ""
    b = set(before.splitlines())
    return ["\n".join(l for l in after.splitlines() if l.strip() and l not in b)[-4000:]]


# ------------------------------------------------------------------ main
def inbox():
    return _load("cm-inbox")


def sender_name():
    """Chi scrive: il nome tmux della sessione da cui parte il comando, o talk.from_name."""
    pane = os.environ.get("TMUX_PANE", "")
    if pane:
        out = tmux("display-message", "-p", "-t", pane, "#{session_name}")
        if out and out.strip():
            return out.strip()
    return CFG["talk"]["from_name"]


def cmd_talk(argv):
    if argv and argv[0] == "--status":
        return inbox().main(["status"] + argv[1:2])
    name = argv[0] if argv else ""
    text = argv[1] if len(argv) > 1 else ""
    if not name or not text:
        die("talk.usage", 2)
    max_wait = int(os.environ.get("ATTESA_MAX", CFG["talk"]["max_wait_s"]))
    quiet = int(os.environ.get("QUIETE", CFG["talk"]["quiet_s"]))
    force = os.environ.get("FORZA") == "si"
    no_wait = False
    via = "auto"
    hint = CFG["talk"]["warn_native_channel"] and os.environ.get("NATIVO") != "no"
    a = argv[2:]
    i = 0
    while i < len(a):
        x = a[i]
        if x in ("--wait", "--attesa"):
            max_wait = int(a[i + 1]); i += 1
        elif x in ("--quiet", "--quiete"):
            quiet = int(a[i + 1]); i += 1
        elif x == "--no-wait":
            no_wait = True
        elif x in ("--force", "--forza"):
            force = True
        elif x == "--via":
            via = a[i + 1]; i += 1
        elif x == "--no-native-hint":
            hint = False
        else:
            die("talk.unknown_option", 2, opt=x)
        i += 1

    row = find(name)
    if not row:
        # 23/09: una sessione nota ma chiusa riceve il messaggio quando riparte (casella persistente); un nome mai
        # visto resta un errore, perche' e' quasi sempre un refuso
        known, cwd = inbox().known_session(name)
        if known:
            rec = inbox().put(name, text, sender_name(), cwd)
            print(M("talk.saved", name=name, id=rec["id"]), file=sys.stderr)
            return 0
        print(M("talk.missing", name=name), file=sys.stderr)
        live = [r["tmux"] or r["name"] for r in sessions.collect(read_screen=False)]
        print("  " + " ".join(live), file=sys.stderr)
        sys.exit(3)
    if row["channel"] == "(questa)":
        die("talk.self", 2, name=name)
    if hint and row["channel"] == "nativo" and os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"):
        print(M("talk.native_hint", name=name), file=sys.stderr)

    # la casella prima della consegna: se la sessione si chiude o il socket rifiuta, il messaggio non si perde
    rec = inbox().put(name, text, sender_name(), row.get("cwd") or "")
    use_socket = via == "socket" or (via == "auto" and row.get("socket") and os.path.exists(row["socket"]) and row.get("session_id"))
    if use_socket:
        row["_transcript"] = transcript_path(row)
        offset = row["_transcript"].stat().st_size if row["_transcript"].exists() else 0
        try:
            post_socket(row, text, CFG["talk"]["from_name"], CFG["talk"]["from_mode"])
        except OSError as e:
            if via == "socket":
                die("talk.socket_failed", 5, name=name, error=str(e))
            print(M("talk.socket_fallback", name=name, error=str(e)), file=sys.stderr)
            use_socket = False
        else:
            inbox().mark(rec["id"], "delivered", "socket")
            print(M("talk.sent", name=name, via="socket"), file=sys.stderr)
            if no_wait:
                return 0
            for t in wait_reply(row, offset, max_wait, quiet):
                print(t)
            return 0
    texts = talk_tmux(row, text, force, max_wait, quiet, no_wait)
    inbox().mark(rec["id"], "delivered", "tmux")
    print(M("talk.sent", name=name, via="tmux"), file=sys.stderr)
    for t in texts:
        print(t)
    return 0


def cmd_wait(argv):
    name = argv[0] if argv else ""
    if not name:
        die("wait.usage", 2)
    timeout = int(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else 12 * 3600
    row = find(name)
    if not row:
        die("talk.missing", 3, name=name)
    row["_transcript"] = transcript_path(row)
    offset = row["_transcript"].stat().st_size if row["_transcript"].exists() else 0
    start = time.time()
    while True:
        st = status_of(row)
        if st in ("idle", "gone"):
            break
        if time.time() - start > timeout:
            print(M("talk.timeout", s=timeout))
            return 1
        time.sleep(2)
    print(M("wait.idle", name=name, status=st))
    # goal nativo ancora aperto (`/goal`): idle con un goal non raggiunto = «Goal paused», non lavoro finito
    g = sessions.goal_of(row)
    if g and not g["met"]:
        print(M("wait.goal", goal=g["condition"], n=g["iterations"]))
    texts, _ = assistant_texts(row["_transcript"], offset)
    if not texts:
        texts, _ = assistant_texts(row["_transcript"], 0)
        texts = texts[-1:]
    for t in texts:
        print(t)
    return 0


if __name__ == "__main__":
    prog = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if args and args[0] in ("talk", "wait"):
        sub, args = args[0], args[1:]
    else:
        sub = "wait" if os.environ.get("CM_SUBCOMMAND") == "wait" else "talk"
    sys.exit(cmd_wait(args) if sub == "wait" else cmd_talk(args))

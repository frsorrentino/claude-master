#!/usr/bin/env python3
"""Hook di sessione di claude-master: un solo script, un evento per invocazione.

    cm-hook.py SessionStart | SessionEnd | UserPromptSubmit | Stop | StopFailure | PermissionRequest

Legge il payload JSON dell'hook da stdin (session_id, cwd, transcript_path, ...)
e fa POCO, in fretta: ogni evento e' un processo in piu' per ogni turno di ogni
sessione. Tutto cio' che scrive sta in `state_dir`:

- registry (1.3): SessionStart/SessionEnd aggiornano il registro delle sessioni
  subito (il cron resta come riconciliazione). SessionEnd NON scatta al riavvio
  della macchina: le voci restano, ed e' esattamente cio' che serve a `restore`.
- waiting/<session_id> (1.8): PermissionRequest lo scrive con il nome del tool
  (AskUserQuestion, un permesso...), UserPromptSubmit lo cancella. `sessions` lo
  legge dopo lo `status: waiting` del registro peer.
- ledger.jsonl (N7): una riga per evento — cosa e' successo, per il diario.
- kernel (D6): SessionStart stampa `kernel_<lingua>.md` (regole di comportamento,
  ~250 token) a startup/resume/clear/compact: dopo una compattazione il testo
  iniettato puo' non esserci piu'.
- ora locale (T46): UserPromptSubmit stampa `[prefisso] <giorno data ora>`.
- Stop: se un riavvio e' armato lo passa a `cm-restart.sh hook`; se la coda della
  sessione (queue/<session_id>) ha voci e `stop_hook_active` e' falso, ne consegna
  UNA con {"decision":"block","reason":...} (E5) — con tetto per turno e scadenza,
  mai un loop.
- StopFailure: ledger + il riavvio armato resta visibile come fallito.
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
STATE = Path(cm.expand(CFG["state_dir"]))

DAYS = {"it": ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def payload():
    try:
        return json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (ValueError, OSError):
        return {}


def ledger(event, p, **extra):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event,
               "session_id": p.get("session_id", ""), "cwd": p.get("cwd", ""),
               "account": account_name(), "pid": os.getppid()}
        row.update(extra)
        with open(STATE / "ledger.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def account_name():
    conf = os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or cm.expand("~/.claude"))
    for name, a in CFG["accounts"].items():
        if os.path.realpath(cm.expand(a["config_dir"])) == conf:
            return name
    return CFG["default_account"]


def registry_update():
    try:
        subprocess.Popen([str(HERE / "cm-registry.sh")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)   # staccato: l'hook non aspetta
    except OSError:
        pass


def local_time():
    h = CFG["hooks"]["local_time"]
    now = datetime.datetime.now()
    day = DAYS.get(CFG["language"], DAYS["en"])[now.weekday()]
    return f"{h['prefix']} " + now.strftime(h["format"]).replace(now.strftime("%A"), day)


def kernel_text(p):
    if not CFG["hooks"]["session_kernel"]["enabled"]:
        return ""
    src = p.get("source", "startup")
    if src not in ("startup", "resume", "clear", "compact", "fork", ""):
        return ""
    f = HERE.parent / f"kernel_{CFG['language']}.md"
    if not f.is_file():
        f = HERE.parent / "kernel_en.md"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def pop_queue(p):
    """Coda 2.5: una voce per Stop, mai con stop_hook_active (il turno viene gia' da un block)."""
    q = STATE / "queue" / p.get("session_id", "-")
    if p.get("stop_hook_active") or not q.is_file():
        return None
    try:
        items = [json.loads(l) for l in q.read_text().splitlines() if l.strip()]
    except ValueError:
        return None
    now = time.time()
    items = [i for i in items if not i.get("expires") or i["expires"] > now]
    if not items:
        q.unlink(missing_ok=True)
        return None
    item, rest = items[0], items[1:]
    if rest:
        q.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in rest) + "\n")
    else:
        q.unlink(missing_ok=True)
    return item


def main(argv):
    ev = argv[0] if argv else ""
    p = payload()
    sid = p.get("session_id", "")
    if ev == "SessionStart":
        registry_update()
        (STATE / "waiting" / sid).unlink(missing_ok=True) if sid else None
        ledger("start", p, source=p.get("source", ""))
        k = kernel_text(p)
        if k:
            sys.stdout.write(k + "\n")
    elif ev == "SessionEnd":
        registry_update()
        ledger("end", p, reason=p.get("reason", ""))
    elif ev == "UserPromptSubmit":
        if sid:
            (STATE / "waiting" / sid).unlink(missing_ok=True)
        if CFG["hooks"]["local_time"]["enabled"]:
            sys.stdout.write(local_time() + "\n")
    elif ev == "PermissionRequest":
        if sid:
            (STATE / "waiting").mkdir(parents=True, exist_ok=True)
            (STATE / "waiting" / sid).write_text(p.get("tool_name", "?"))
        ledger("waiting", p, tool=p.get("tool_name", ""))
    elif ev == "Stop":
        last = (p.get("last_assistant_message") or "")[:300]
        ledger("stop", p, last=last)
        if CFG["hooks"]["restart_stop"]["enabled"]:
            r = subprocess.run([str(HERE / "cm-restart.sh"), "hook"], input=json.dumps(p), capture_output=True, text=True,
                               env={**os.environ, "CM_HOOK_SESSION_ID": sid})
            if r.stdout.strip():
                sys.stdout.write(r.stdout)
                return 0
        item = pop_queue(p)
        if item:
            ledger("queue-pop", p, text=item.get("text", "")[:120])
            sys.stdout.write(json.dumps({"decision": "block",
                                         "reason": f"[claude-master queue] {item.get('text', '')}"}, ensure_ascii=False) + "\n")
    elif ev == "StopFailure":
        ledger("stop-failure", p, error=str(p.get("error", ""))[:200])
        subprocess.run([str(HERE / "cm-restart.sh"), "failed"], input=json.dumps(p), capture_output=True, text=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

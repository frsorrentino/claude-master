#!/usr/bin/env python3
"""claude-master bot — lanciare la sessione della radice (o un progetto) dal telefono quando
NESSUNA sessione è viva, riusando il bot Telegram del plugin `telegram` di Claude Code (N2, ridotta).

  claude-master bot poll        un giro di getUpdates (dal cron, ogni bot.cron_minutes); zero se spento
  claude-master bot install     scrive la riga di cron (bot.enabled deve essere true)
  claude-master bot uninstall   la toglie
  claude-master bot status      abilitato, cron, token, chat autorizzate, poller del plugin, offset, log

Tre soli comandi accettati, SOLO da chat private in `allowFrom` di access.json; tutto il resto
si ignora (le chat non autorizzate in silenzio, quelle autorizzate con la lista dei comandi):

  /master             launch della radice (workspace.root) senza finestra, risposta col link
  /launch <frammento> risolve il percorso come la skill: un candidato → lancia; più di uno o
                      nessuno → elenca e NON crea
  /sessions           l'output di `claude-master sessions`
  /recap              il recap di oggi (come alle 20:00; /diary è un alias)

Telegram consegna gli update a UN solo consumatore per token: il plugin `telegram` delle sessioni
vive fa polling e scrive bot.pid. Questo poller gira SOLO se quel pid è assente o morto, altrimenti
si ruberebbero i messaggi a vicenda (scelta di Franz del 10/09/2026: stesso bot, non un secondo).
Al primo giro (nessun offset salvato) l'arretrato si SCARTA: un «/master» di ieri non deve
lanciare niente oggi. Ogni update confermato appena trattato (offset scritto subito): un crash a
metà non ripete un lancio. Un lock evita due giri sovrapposti. Nessuna dipendenza: urllib.

Prove: CM_BOT_CM (dispatcher da usare per launch/sessions), bot.api_base (server finto), CM_CRONTAB_CMD.
"""
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
B = CFG["bot"]
CM_BIN = os.environ.get("CM_BOT_CM") or str(HERE / "claude-master")
COMMANDS = ("/master", "/launch", "/sessions", "/recap")
MAX_TEXT = 3900   # Telegram: 4096 caratteri per messaggio


# ------------------------------------------------------------------ file di stato e credenziali
def offset_path():
    return Path(cm.expand(B["offset_file"]))


def log_path():
    return Path(cm.expand(B["log"]))


def log(line):
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def token():
    """TELEGRAM_BOT_TOKEN dal file .env del plugin (KEY=VALUE, apici tollerati)."""
    p = Path(cm.expand(B["token_file"]))
    try:
        for raw in p.read_text().splitlines():
            k, _, v = raw.strip().partition("=")
            if k.strip() == "TELEGRAM_BOT_TOKEN":
                return v.strip().strip("'\"")
    except OSError:
        return ""
    return ""


def allowed_chats():
    """Le chat autorizzate: `allowFrom` di access.json (stringhe di id)."""
    try:
        d = json.loads(Path(cm.expand(B["access_file"])).read_text())
    except (OSError, ValueError):
        return set()
    return {str(x) for x in d.get("allowFrom", [])}


def plugin_poller_pid():
    """Il pid del poller del plugin telegram, se VIVO; altrimenti 0."""
    try:
        pid = int(Path(cm.expand(B["pid_file"])).read_text().strip() or 0)
    except (OSError, ValueError):
        return 0
    if pid <= 0:
        return 0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return 0
    except PermissionError:
        return pid
    return pid


# ------------------------------------------------------------------ Telegram
def api(method, **params):
    url = f"{B['api_base'].rstrip('/')}/bot{token()}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=int(B["http_timeout_s"])) as r:
        return json.loads(r.read().decode())


def reply(chat_id, text, parse_mode=None):
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n…"
    try:
        api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true", parse_mode=parse_mode)
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"reply to {chat_id} FAILED: {e}")


# ------------------------------------------------------------------ comandi
def run_cm(*args):
    p = subprocess.run([CM_BIN, *args], capture_output=True, text=True, timeout=int(B["command_timeout_s"]))
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return p.returncode, out


def resolve(fragment):
    """I candidati sotto la radice come la skill: `<radice>/*/` e `<radice>/*/*/` dentro le
    project_dirs, senza le excluded_dirs, filtrati sul frammento (sottostringa del nome, senza
    maiuscole). Un nome IDENTICO al frammento vince da solo."""
    ws = CFG["workspace"]
    root = Path(cm.expand(ws["root"]))
    excluded = set(ws.get("excluded_dirs") or [])
    project = [p for p in (ws.get("project_dirs") or ["."])]
    frag = fragment.strip().lower()
    if not frag:
        return []
    found = []
    for pd in project:
        base = root if pd in (".", "") else root / pd
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name in excluded or d.name.startswith("."):
                continue
            found.append(d)
            if pd in (".", ""):
                for dd in sorted(d.iterdir()):
                    if dd.is_dir() and dd.name not in excluded and not dd.name.startswith("."):
                        found.append(dd)
    hits = [d for d in found if frag in d.name.lower()]
    exact = [d for d in hits if d.name.lower() == frag]
    return exact if len(exact) == 1 else hits


def do_master():
    root = cm.expand(CFG["workspace"]["root"])
    rc, out = run_cm("launch", root, "--no-window")
    return (M("bot.launched", dir=root) + "\n" + out) if rc == 0 else (M("bot.launch_failed", dir=root) + "\n" + out)


def do_launch(fragment):
    if not fragment.strip():
        return M("bot.launch_usage")
    hits = resolve(fragment)
    if len(hits) == 1:
        rc, out = run_cm("launch", str(hits[0]), "--no-window")
        return (M("bot.launched", dir=hits[0]) + "\n" + out) if rc == 0 else (M("bot.launch_failed", dir=hits[0]) + "\n" + out)
    if not hits:
        return M("bot.no_match", frag=fragment)
    cap = int(B["max_candidates"])
    root = cm.expand(CFG["workspace"]["root"])
    names = [str(h).replace(root.rstrip("/") + "/", "") for h in hits[:cap]]
    more = f"\n… (+{len(hits) - cap})" if len(hits) > cap else ""
    return M("bot.ambiguous", frag=fragment, n=len(hits)) + "\n" + "\n".join(names) + more


def do_sessions():
    rc, out = run_cm("sessions")
    return out or M("bot.no_output")


def handle(text):
    """Il testo di un messaggio autorizzato → la risposta (o None se non è un comando)."""
    t = text.strip()
    if not t.startswith("/"):
        return M("bot.help")
    cmd, _, rest = t.partition(" ")
    cmd = cmd.split("@", 1)[0].lower()   # «/master@nomebot» nei gruppi e nei suggerimenti
    if cmd == "/master":
        return do_master()
    if cmd == "/launch":
        return do_launch(rest)
    if cmd == "/sessions":
        return do_sessions()
    if cmd in ("/recap", "/diary"):
        d = _load("cm-recap")
        _, _, groups, label = d.build(["--full"] if rest.strip() in ("full", "tutto") else [])
        return d.render_short(groups, label, as_html=False)
    return M("bot.help")


# ------------------------------------------------------------------ poll
def poll():
    if not B["enabled"]:
        return 0
    pid = plugin_poller_pid()
    if pid:
        return 0   # il plugin ascolta: niente da fare, e niente rumore nel log ogni minuto
    if not token():
        log("no token: " + str(B["token_file"]))
        return 1
    off_p = offset_path()
    off_p.parent.mkdir(parents=True, exist_ok=True)
    lock = open(str(off_p) + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0   # un giro precedente è ancora in corso
    try:
        first = not off_p.is_file()
        offset = None
        if not first:
            try:
                offset = int(off_p.read_text().strip() or 0) or None
            except ValueError:
                offset = None
        try:
            r = api("getUpdates", offset=offset, timeout=0, allowed_updates='["message"]')
        except urllib.error.HTTPError as e:
            if e.code == 409:
                log("409 conflict: another poller holds the token (plugin just started?)")
                return 0
            log(f"getUpdates HTTP {e.code}")
            return 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            log(f"getUpdates failed: {e}")
            return 1
        updates = r.get("result", []) if isinstance(r, dict) else []
        if first:
            # arretrato scartato, tranne i messaggi degli ultimi first_run_max_age_s secondi: il
            # primo giro capita quando l'ultima sessione si chiude, e un «/master» mandato in quel
            # minuto e' proprio quello che si aspetta una risposta
            fresh = time.time() - int(B["first_run_max_age_s"])
            old = [u for u in updates if int((u.get("message") or {}).get("date") or 0) < fresh]
            updates = [u for u in updates if u not in old]
            last = max((u.get("update_id", 0) for u in old), default=0)
            if not updates:
                off_p.write_text(str(last + 1 if old else 0))
            log(f"first run: {len(old)} old update(s) discarded, {len(updates)} recent kept")
        allowed = allowed_chats()
        for u in updates:
            uid = u.get("update_id", 0)
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            frm = (msg.get("from") or {}).get("id")
            text = msg.get("text") or ""
            authorized = chat.get("type") == "private" and (str(chat_id) in allowed or str(frm) in allowed)
            if not authorized:
                log(f"ignored update {uid}: chat {chat_id} ({chat.get('type')}) not allowed")
            elif not text:
                log(f"ignored update {uid}: no text from {chat_id}")
            else:
                head = text.strip().split()[0] if text.strip() else ""
                log(f"update {uid} from {chat_id}: {head[:40]}")
                try:
                    answer = handle(text)
                except subprocess.TimeoutExpired:
                    answer = M("bot.timeout")
                log(f"  → {answer.splitlines()[0][:120] if answer else '-'}")
                reply(chat_id, answer)
            off_p.write_text(str(uid + 1))
        return 0
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


# ------------------------------------------------------------------ cron
def cron_line():
    shim = cm.home() / ".local" / "bin" / "claude-master"
    m = max(1, int(B["cron_minutes"]))
    return f"{'* ' if m == 1 else f'*/{m} '}* * * * {shim} bot poll >/dev/null 2>&1"


def crontab_read():
    ct = os.environ.get("CM_CRONTAB_CMD", "crontab")
    return subprocess.run([ct, "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    ct = os.environ.get("CM_CRONTAB_CMD", "crontab")
    subprocess.run([ct, "-"], input=text, text=True, check=True)


def install():
    if not B["enabled"]:
        print(M("bot.disabled"))
        return 2
    if not token():
        print(M("bot.no_token", path=B["token_file"]))
        return 1
    cur = crontab_read()
    if "claude-master bot poll" in cur:
        print(M("bot.cron_present"))
        return 0
    new = cur.rstrip("\n") + ("\n" if cur.strip() else "") + "# claude-master bot: /master, /launch, /sessions dal telefono a sessioni chiuse\n" + cron_line() + "\n"
    crontab_write(new)
    print(M("bot.cron_installed", line=cron_line()))
    return 0


def uninstall():
    cur = crontab_read()
    if "claude-master bot poll" not in cur:
        print(M("bot.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "claude-master bot" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("bot.cron_removed"))
    return 0


def status():
    print(M("bot.status_enabled", state="true" if B["enabled"] else "false"))
    print(M("bot.status_cron", state="yes" if "claude-master bot poll" in crontab_read() else "no", line=cron_line()))
    print(M("bot.status_token", state="ok" if token() else "MISSING", path=B["token_file"]))
    print(M("bot.status_chats", n=len(allowed_chats()), path=B["access_file"]))
    pid = plugin_poller_pid()
    print(M("bot.status_plugin", state=(M("bot.plugin_alive", pid=pid) if pid else M("bot.plugin_dead"))))
    off = offset_path().read_text().strip() if offset_path().is_file() else "-"
    print(M("bot.status_offset", offset=off, path=offset_path()))
    lp = log_path()
    if lp.is_file():
        tail = lp.read_text().splitlines()[-5:]
        print(M("bot.status_log", path=lp))
        for l in tail:
            print("  " + l)
    return 0


def main(argv):
    verb = argv[0] if argv else "status"
    if verb == "poll":
        return poll()
    if verb == "install":
        return install()
    if verb == "uninstall":
        return uninstall()
    if verb == "status":
        return status()
    if verb == "resolve":   # solo per le prove e la curiosità: i candidati di un frammento
        for h in resolve(" ".join(argv[1:])):
            print(h)
        return 0
    print(M("bot.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

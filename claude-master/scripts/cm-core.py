"""Il cuore condiviso di claude-master: quello che serve sia al relay del polso sia al bot.

Nasce il 16/09/2026 dal ritiro del bot interattivo di Telegram (passo 1 del piano): prima il relay
prendeva queste funzioni da cm-bot.py e da cm-bot-ui.py, cioe' l'app al polso dipendeva dal modulo di
Telegram. Qui non c'e' niente di Telegram: il registro degli eventi, il transcript di una sessione,
l'icona della scheda, il rilancio di una sessione chiusa e la lettura del transcript di Claude Code.

Ci sono anche le poche funzioni di testo che servivano agli avvisi (riga intera, nome corto, sintesi
della domanda): venivano da cm-bot-ui.py, che con il bot interattivo non esiste piu'.

La configurazione si carica PIGRA (_cfg, una volta sola): cosi' importare questo modulo non costa I/O.
Testato da tests/core-verify.py, e dal vivo dalle suite del relay e di answer.
"""
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        _CFG = cm.load(warn=False)
    return _CFG


def cm_bin():
    """Il dispatcher da usare: i test ne mettono uno finto (CM_CORE_CM, o quello del bot per le sue suite)."""
    return os.environ.get("CM_CORE_CM") or os.environ.get("CM_BOT_CM") or str(HERE / "claude-master")


def run_cm(*args):
    timeout = int((_cfg().get("bot") or {}).get("command_timeout_s") or 60)
    p = subprocess.run([cm_bin(), *args], capture_output=True, text=True, timeout=timeout)
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return p.returncode, out


def prefixes():
    return [a.get("tmux_prefix") or "" for a in _cfg()["accounts"].values()]


_ICONS = {}


def icon_of(name):
    """L'icona della sessione (registro colori, stessa della scheda del Terminale); cm-color.sh la
    assegna se manca, con la logica di launch."""
    if not name:
        return ""
    if name not in _ICONS:
        try:
            r = subprocess.run([str(HERE / "cm-color.sh"), name], capture_output=True, text=True, timeout=10)
            _ICONS[name] = (r.stdout.split() or [""])[0] if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            _ICONS[name] = ""
    return _ICONS[name]


def _epoch(ts):
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(str(ts)[:19]).timestamp()
    except ValueError:
        return 0.0


def ledger_rows():
    p = Path(cm.expand(_cfg()["state_dir"])) / "ledger.jsonl"
    out = []
    try:
        for line_ in p.read_text().splitlines():
            try:
                out.append(json.loads(line_))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def transcript_of(row):
    """Il transcript jsonl della sessione (come cm-talk): <config_dir>/projects/<slug>/<sessionId>.jsonl, o ""."""
    if not row or not row.get("session_id") or not row.get("cwd"):
        return ""
    acc = _cfg()["accounts"].get(row.get("account") or "") or {}
    conf = cm.expand(acc.get("config_dir", "~/.claude"))
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(row["cwd"]))
    p = Path(conf) / "projects" / slug / f"{row['session_id']}.jsonl"
    return str(p) if p.is_file() else ""


def reopen(name, run=None):
    """Rilancia una sessione sparita (✗) nella sua cartella, con il suo account e senza finestra (dal telefono o dal
    polso non serve; 15/09, dall'orologio: una sessione chiusa non si poteva riprendere). La conversazione: `--resume
    <id>` se la fotografia del registro ha l'id e il suo transcript esiste (con un id inesistente claude apre una
    conversazione VUOTA senza errore); altrimenti `--continue`, ma solo se nella cartella non c'e' un'altra sessione
    viva, perche' -c prende l'ultima conversazione della cartella, che puo' essere quella dell'altra. Rifiuta se una
    sessione viva ha gia' quel nome. Torna (ok, codice, campi): i testi li sceglie chi chiama (bot.reopen_*,
    relay.cmd_reopen_*). `run` e' il dispatcher di chi chiama: il relay ha il suo (CM_RELAY_CM)."""
    run = run or run_cm

    def _j(*args, expect):
        try:
            rc, out = run(*args)
            return json.loads(out) if rc == 0 and out.strip().startswith(expect) else None
        except (ValueError, subprocess.TimeoutExpired):
            return None

    def _here(r):
        return r.get("cwd") and os.path.realpath(r["cwd"]) == os.path.realpath(cwd)
    live = [r for r in (_j("sessions", "--json", expect="[") or []) if r.get("tmux") or r.get("name")]
    if any((r.get("tmux") or r.get("name")) == name for r in live):
        return False, "alive", {}
    s = next((x for x in (_j("registry", "--good", expect="{") or {}).get("sessioni", []) if x.get("nome") == name), None)
    if not s:
        return False, "unknown", {}
    cwd, acct, sid = s.get("cartella") or "", s.get("account") or "", s.get("session_id") or ""
    if not cwd or not os.path.isdir(cwd):
        return False, "no_dir", {"path": cwd or "?"}
    # --window esplicito (15/09, prova dal polso: la sessione ripartiva ma sul desktop non c'era): launch recupera il
    # display anche da un daemon nato da cron; senza desktop si lancia comunque, senza finestra
    args = ["launch", cwd, "--window"] + (["--account", acct] if acct else [])
    if sid and transcript_of({"session_id": sid, "cwd": cwd, "account": acct}):
        args += ["--resume", sid]; code = "done"
    else:
        same = [r for r in live if _here(r)]
        if same:
            return False, "ambiguous", {"n": len(same), "path": cwd}
        args += ["--continue"]; code = "done_last"
    try:
        rc, out = run(*args)
    except subprocess.TimeoutExpired:
        return False, "failed", {"line": "timeout"}
    if rc != 0:
        return False, "failed", {"line": (out.splitlines() or ["launch failed"])[0]}
    # launch prende il primo nome libero: se non e' il vecchio, il vecchio esce dalla fotografia (restava ✗ per sempre)
    before = {r.get("tmux") or r.get("name") for r in live}
    new = [r.get("tmux") or r.get("name") for r in (_j("sessions", "--json", expect="[") or [])
           if (r.get("tmux") or r.get("name")) not in before and _here(r)]
    if new and name not in new:
        run("registry", "--closed", name)
    return True, code, {"account": acct or "?", "new": new[0] if new else name}


# ------------------------------------------------------------------ modello, effort e contesto di una sessione
WINDOW_1M = 1_000_000
WINDOW_STD = 200_000


def _identity_in(lines):
    """L'ULTIMA voce di sistema col modello fra queste righe, o None."""
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("type") == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "model" and (a.get("identity") or {}).get("modelId"):
                return a["identity"]
    return None


def session_runtime(row, tail_bytes=512 * 1024, head_bytes=1024 * 1024):
    """(contratto 1.11, 16/09/2026) Cosa sta usando una sessione, letto dalla sua trascrizione:
    {"model": {"id","label"} | None, "effort": "high" | None, "context": 0-100 | None}.

    L'ultimo turno dell'assistente porta il modello (senza la finestra) e l'effort; una voce di sistema
    porta l'id completo e il nome («claude-opus-5[1m]», «Opus 5 (1M context)»), da cui si sa se la finestra
    e' da 1M o standard. Il contesto e' la somma dei token dell'ultimo turno (input + cache letta + cache
    scritta) sulla finestra. Se i due modelli non coincidono — un cambio di modello a meta' sessione — la
    finestra non e' certa e `context` resta ASSENTE invece di essere stimato: e' la regola chiesta dall'app."""
    out = {"model": None, "effort": None, "context": None}
    path = transcript_of(row)
    if not path:
        return out
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            data = f.read()
    except OSError:
        return out
    lines = data.split(b"\n")
    if size > tail_bytes and lines:
        lines = lines[1:]   # la prima riga della coda e' mozza
    assistant = None
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("type") == "assistant" and isinstance(d.get("message"), dict):
            assistant = d
            break
    if assistant is None:
        return out
    # 1.11.1 (16/09, dall'app: label e context sempre nulli dal vivo): la voce di sistema col modello la scrive
    # Claude Code all'AVVIO, quindi in una sessione lunga sta ben prima della coda — nella mia, a 221 KB su 4,5 MB.
    # Si guarda prima la coda (un cambio di modello a meta' sessione ne riscrive una li'), poi la testa. La testa e'
    # larga 1 MB perche' quella voce non e' la prima riga del file: viene dopo il prompt di sistema, i memo e gli
    # hook del progetto, e in questa sessione stava a 221 KB — con 128 KB restava fuori e il campo tornava vuoto.
    identity = _identity_in(lines)
    if identity is None and size > tail_bytes:
        try:
            with open(path, "rb") as f:
                head = f.read(head_bytes)
            identity = _identity_in(head.split(b"\n")[:-1])   # l'ultima riga della testa e' mozza
        except OSError:
            identity = None
    model_id = str(assistant["message"].get("model") or "")
    if assistant.get("effort"):
        out["effort"] = str(assistant["effort"])
    full_id = str((identity or {}).get("modelId") or "")
    same = bool(full_id) and full_id.split("[")[0] == model_id
    if same:
        label = str((identity or {}).get("marketingName") or "").split(" (")[0].strip()
        out["model"] = {"id": full_id, "label": label or None}
        usage = assistant["message"].get("usage") or {}
        tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        window = WINDOW_1M if "[1m]" in full_id else WINDOW_STD
        if tokens:
            out["context"] = max(0, min(100, round(tokens * 100 / window)))
    elif model_id:
        # modello cambiato a meta' sessione (o voce di sistema assente): l'id si sa, la finestra no
        out["model"] = {"id": model_id, "label": None}
    return out


# ------------------------------------------------------------------ il polso riceve? (scorta su Telegram)
def relay_dir():
    return Path(cm.expand((_cfg().get("relay") or {}).get("dir") or "~/.claude-master/relay"))


def relay_down_for(now=None):
    """Da quanti secondi il polso non riceve: il relay scrive fallback.json {"since": epoch} quando una push o
    una sveglia FCM fallisce, e lo cancella appena ne va a buon fine una. 0 se tutto funziona (16/09, passo 3 del
    ritiro di Telegram: sotto la soglia si tace, perche' un singolo errore di rete si recupera da solo)."""
    try:
        since = float(json.loads((relay_dir() / "fallback.json").read_text()).get("since") or 0)
    except (OSError, ValueError, AttributeError):
        return 0.0
    return max(0.0, (now or time.time()) - since) if since else 0.0


def watch_receiving(now=None):
    """L'orologio e' accoppiato e riceve: c'e' un dispositivo e l'ultima push e' andata bene da poco (la stessa
    regola che teneva muto Telegram quando l'app avvisa gia' lei)."""
    fresh = float((_cfg().get("bot") or {}).get("watch_fresh_s") or 180)
    try:
        devices = json.loads((relay_dir() / "devices.json").read_text())
        last = json.loads((relay_dir() / "last-state.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(devices) and (now or time.time()) - float(last.get("pushed_at") or 0) < fresh


# ------------------------------------------------------------------ testo a misura di polso e di telefono
WIDTH = 22        # larghezza utile di uno schermo tondo: solo le etichette corte ci passano
MAX_LINES = 8     # righe di un avviso: oltre, si legge male sia sul polso sia nella bolla
NAME_MAX = 14     # il nome di una sessione in una riga, senza il prefisso dell'account


def short_name(name, prefixes=(), n=NAME_MAX):
    for p in sorted((p for p in prefixes if p), key=len, reverse=True):
        if name.startswith(p):
            name = name[len(p):]
            break
    return name[:n]


def line(text):
    """Una riga logica = una riga fisica (l'utente 12/09 11:14, screenshot di telefono e watch): spazi normalizzati,
    NESSUN taglio ne' a capo interno — la bolla di Telegram si allarga quanto la riga piu' lunga, e a 22
    caratteri restava a meta' schermo col testo mozzato. Solo le etichette dei bottoni passano da fit()."""
    return " ".join(str(text or "").split())


TOOL_INPUT_KEYS = ("command", "file_path", "pattern", "path", "query", "url", "prompt", "description")


def join(*parts):
    """Righe corte adiacenti dello stesso tipo unite con « · », cosi' la prima riga e' la piu' lunga possibile."""
    return " · ".join(line(p) for p in parts if line(p))


def question_gist(text, max_chars=88):
    """La domanda in breve, deterministica (l'utente 12/09 10:54: tagliata non si puo' rispondere): tutta se sta in
    max_chars; altrimenti l'ULTIMA frase interrogativa che ci sta (il contesto prima si lascia); altrimenti ""
    (serve una sintesi: la fa cm-answer col modello)."""
    t = " ".join(str(text or "").split())
    if len(t) <= max_chars:
        return t
    for s in reversed(re.findall(r"[^.!?]*\?", t)):
        s = s.strip()
        if s and len(s) <= max_chars:
            return s
    return ""


def tool_note(tool_input):
    """La `description` che Claude scrive accanto a un comando Bash: dice l'INTENTO («Run the plugin test suite»)
    dove il comando dice solo «cd …» (contratto 1.5, chiesto dal polso il 14/09). "" se non c'e'."""
    if isinstance(tool_input, dict):
        d = " ".join(str(tool_input.get("description") or "").split())
        return d[:120]
    return ""


def tool_line(name, tool_input, width=120):
    """«Bash git log --since yesterday»: il tool e l'input (fino a `width` caratteri, senza segno di taglio), come
    lo spinner del desktop."""
    detail = ""
    if isinstance(tool_input, dict):
        for k in TOOL_INPUT_KEYS:
            if tool_input.get(k):
                detail = " ".join(str(tool_input[k]).split())
                break
    elif tool_input:
        detail = " ".join(str(tool_input).split())
    return line(f"{name} {detail[:width]}")


def transcript_events(path, offset):
    """Gli eventi del transcript (jsonl di Claude Code) scritti dopo `offset` byte: ("user", testo),
    ("text", testo assistant), ("tool", nome, input). Torna (eventi, nuovo offset). Righe rotte ignorate."""
    events = []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return events, offset
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        kind = d.get("type")
        content = (d.get("message") or {}).get("content")
        if kind == "user":
            if isinstance(content, str):
                events.append(("user", content))
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        events.append(("user", b["text"]))
        elif kind == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    events.append(("text", b["text"]))
                elif b.get("type") == "tool_use":
                    events.append(("tool", str(b.get("name") or "?"), b.get("input")))
    return events, offset + len(data)

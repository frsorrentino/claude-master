#!/usr/bin/env python3
"""claude-master bot — lanciare la sessione della radice (o un progetto) dal telefono quando
NESSUNA sessione è viva, riusando il bot Telegram del plugin `telegram` di Claude Code (N2, ridotta).

  claude-master bot serve       il daemon: long polling (getUpdates con timeout=50, Telegram risponde all'istante),
                                riconnessione a scalare (1 2 5 15 30 s) su errori di rete, lock esclusivo, pidfile
  claude-master bot ensure      rilancia `serve` se non gira (dal cron ogni minuto e dal restore)
  claude-master bot poll        un giro di getUpdates: ripiego manuale, saltato se `serve` e' vivo
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
  /start              «vuoi /master?» con un bottone inline (Franz l'ha scritto due volte credendo di
                      lanciare la master, 11/09/2026); il tap e' un callback_query che, a sessioni chiuse,
                      arriva a QUESTO poller: lancia la master come /master

Telegram consegna gli update a UN solo consumatore per token: il plugin `telegram` delle sessioni
vive fa polling e scrive bot.pid. Questo poller gira SOLO se quel pid è assente o morto, altrimenti
si ruberebbero i messaggi a vicenda (scelta di Franz del 10/09/2026: stesso bot, non un secondo).
Al primo giro (nessun offset salvato) l'arretrato si SCARTA: un «/master» di ieri non deve
lanciare niente oggi. Ogni update confermato appena trattato (offset scritto subito): un crash a
metà non ripete un lancio. Un lock evita due giri sovrapposti. Nessuna dipendenza: urllib.

A misura di SMARTWATCH (Wear OS, mandato di Franz 11/09/2026 16:36–16:40; le regole di resa in
cm-bot-ui.py): ogni messaggio ≤ 8 righe da ≤ 22 caratteri; comandi a parola nuda dettabile («sessioni»,
«lancia shop-acme», «due si»); bottoni contestuali (Sessioni, «◀ nome», Avvisami, Continua, Ferma, Terminale); stato per chat
(elenco / scheda di una sessione, scadenza 10 min); in scheda un numero risponde alla domanda con
`answer NOME N` (prima un checkpoint git del workspace: «Annulla modifiche» lo ripristina); «Avvisami» segue
una sessione (avviso «✓ nome ha finito:» a fine di un turno > follow_min_turn_s, «✗ nome» se sparisce); `bot digest`
alle 8:00. Tutto silenzioso (disable_notification) tranne domande, esiti, risposte, errori e sparizioni.

Un PROMPT dal watch (12/09/2026, parita' col desktop): il prompt parte con il prefisso «Da Franz via Telegram
(watch)…» che chiede di chiudere con una riga «Watch: <esito ≤ 60 caratteri>»; la risposta e' UN messaggio vivo
«📤 nome» + eco, editato dal transcript della sessione ogni LIVE_EDIT_S («▶ nome al lavoro», «▶ nome · 1m» + il
tool in corso + le ultime righe di testo, «❓ nome aspetta te»), con i tasti Ferma (Esc in tmux) · Terminale ·
Sessioni; «⚠ nome non ha ricevuto» + Invia di nuovo se il prompt non compare nel transcript entro
RECEIVE_TIMEOUT_S; a fine turno un messaggio NUOVO «✓ nome» + la riga Watch: (Leggi tutto per il resto). La
risposta e' lo Stop con la riga Watch:, o il primo se la sessione era ferma all'invio, o il secondo se era
occupata (il primo e' il turno precedente, mostrato come «ha finito»).

Prove: CM_BOT_CM (dispatcher da usare per launch/sessions/answer/...), bot.api_base (server finto), CM_CRONTAB_CMD.
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
ui = _load("cm-bot-ui")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
B = CFG["bot"]
CM_BIN = os.environ.get("CM_BOT_CM") or str(HERE / "claude-master")
COMMANDS = ("/master", "/launch", "/sessions", "/recap")
MAX_TEXT = 3900   # Telegram: 4096 caratteri per messaggio
STATE_TTL_S = 600   # scaduta la scheda, un numero torna a voler dire «apri la scheda N»
SERVE_BACKOFF = [float(x) for x in os.environ.get("CM_BOT_BACKOFF", "1 2 5 15 30").split()]
TURN_MIN_S = int(B.get("follow_min_turn_s") or 30)   # in una sessione SEGUITA, un turno piu' corto non merita l'avviso
# il messaggio VIVO di un prompt dal watch (12/09/2026): un solo messaggio editato dal transcript mentre la
# sessione lavora (come guardare il terminale ogni pochi secondi), mai messaggi nuovi finche' non finisce
LIVE_EDIT_S = float(B.get("live_edit_s") or 3)             # cadenza minima degli edit (Telegram tollera ~1/s per chat)
RECEIVE_TIMEOUT_S = float(B.get("receive_timeout_s") or 15)   # senza traccia del prompt nel transcript: «non ha ricevuto»
ANSWER_TIMEOUT_S = float(B.get("answer_timeout_s") or 1800)   # un solo «ancora al lavoro» silenzioso, poi si aspetta lo Stop
WATCH_MARK = "via Telegram (watch)"   # il pezzo del prefisso che si ritrova nel transcript: il prompt e' arrivato


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
def api(method, _http_timeout=None, **params):
    url = f"{B['api_base'].rstrip('/')}/bot{token()}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=float(_http_timeout or B["http_timeout_s"])) as r:
        return json.loads(r.read().decode())


def reply(chat_id, text, parse_mode=None, reply_markup=None, silent=False, reply_to=None):
    """Manda; torna il message_id (o None). `silent` = disable_notification: il polso vibra solo per le
    domande, gli esiti seguiti e le sparizioni."""
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n…"
    try:
        r = api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true", parse_mode=parse_mode,
                reply_markup=json.dumps(reply_markup, ensure_ascii=False) if reply_markup else None,
                disable_notification="true" if silent else None, reply_to_message_id=reply_to)
        return ((r or {}).get("result") or {}).get("message_id")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"reply to {chat_id} FAILED: {e}")
        return None


def edit(chat_id, message_id, text, reply_markup=None):
    """Edita un messaggio (il messaggio vivo): niente notifica, niente messaggio nuovo. «not modified» non e' un errore."""
    if not message_id:
        return False
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n…"
    try:
        api("editMessageText", chat_id=chat_id, message_id=message_id, text=text, disable_web_page_preview="true",
            reply_markup=json.dumps(reply_markup, ensure_ascii=False) if reply_markup else None)
        return True
    except urllib.error.HTTPError as e:
        if e.code != 400:
            log(f"edit {message_id} in {chat_id} FAILED: HTTP {e.code}")
        return False
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"edit {message_id} in {chat_id} FAILED: {e}")
        return False


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


def alive_at(directory):
    """La riga di `sessions --json` viva su quella cartella, o None (dal polso, 16:30 dell'11/09/2026:
    /master con la master viva lanciava lo stesso)."""
    try:
        rc, out = run_cm("sessions", "--json")
        rows = json.loads(out) if rc == 0 and out.strip().startswith("[") else []
    except (ValueError, subprocess.TimeoutExpired):
        rows = []
    target = os.path.realpath(str(directory))
    for r in rows:
        if os.path.realpath(str(r.get("cwd") or "")) == target and (r.get("tmux") or r.get("name")):
            return r
    return None


def already_alive(row):
    return M("bot.already_alive", name=row.get("tmux") or row.get("name"), status=row.get("status") or "?", link=row.get("link") or "")


# ------------------------------------------------------------------ stato per chat
def state_path():
    return Path(cm.expand(B.get("state_file") or "")) if B.get("state_file") else Path(cm.expand(CFG["state_dir"])) / "bot-state.json"


def state_load():
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def state_save(st):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


def chat_state(st, chat_id):
    return st.setdefault("chats", {}).setdefault(str(chat_id), {"level": "list", "session": "", "until": 0, "list": [],
                                                                "options": [], "follow": [], "seen": {}, "qmsg": None,
                                                                "checkpoints": {}})


def prefixes():
    return [a.get("tmux_prefix") or "" for a in CFG["accounts"].values()]


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


def icons_for(rows):
    return {r.get("tmux") or r.get("name"): icon_of(r.get("tmux") or r.get("name")) for r in rows}


def label_of(name):
    ic = icon_of(name)
    return f"{ic} {ui.short_name(name, prefixes())}".strip()


# ------------------------------------------------------------------ sorgenti: sessioni, ledger, recap, domande
def _epoch(ts):
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(str(ts)[:19]).timestamp()
    except ValueError:
        return 0.0


def ledger_rows():
    p = Path(cm.expand(CFG["state_dir"])) / "ledger.jsonl"
    out = []
    try:
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def stops_of(session_id, rows=None):
    """[(epoch, esito, epoch dell'evento precedente)] degli stop di una sessione, in ordine; l'esito e'
    la riga «Esito:» o l'ultima riga del messaggio (campi `esito`/`tail` dell'hook), pulita."""
    rows = rows if rows is not None else ledger_rows()
    out, prev = [], None
    for r in rows:
        if r.get("session_id") != session_id:
            continue
        t = _epoch(r.get("ts"))
        if r.get("event") == "stop":
            out.append((t, ui.esito_of(str(r.get("last") or ""), str(r.get("esito") or r.get("tail") or "")), prev))
        prev = t
    return out


def stops_full(session_id, rows=None):
    """Come stops_of, con in piu' il testo intero disponibile (tail o esito o last) e la riga «Watch:» (campo
    dell'hook, o cercata nel testo) per la risposta al watch: [(epoch, esito, prev, full, watch)]."""
    rows = rows if rows is not None else ledger_rows()
    out, prev = [], None
    for r in rows:
        if r.get("session_id") != session_id:
            continue
        t = _epoch(r.get("ts"))
        if r.get("event") == "stop":
            esito = ui.esito_of(str(r.get("last") or ""), str(r.get("esito") or r.get("tail") or ""))
            full = str(r.get("esito") or "") + ("\n" if r.get("esito") and r.get("tail") else "") + str(r.get("tail") or r.get("last") or "")
            watch = ui.watch_line(str(r.get("watch") or "")) or ui.watch_line(full)
            out.append((t, esito, prev, full, watch))
        prev = t
    return out


def failures_of(session_id, rows=None):
    """[(epoch, errore)] degli stop-failure di una sessione (hook StopFailure): il watch li vede come «✗ nome errore»."""
    rows = rows if rows is not None else ledger_rows()
    return [(_epoch(r.get("ts")), str(r.get("error") or "")) for r in rows
            if r.get("session_id") == session_id and r.get("event") == "stop-failure"]


def transcript_of(row):
    """Il transcript jsonl della sessione (come cm-talk): <config_dir>/projects/<slug>/<sessionId>.jsonl, o ""."""
    if not row or not row.get("session_id") or not row.get("cwd"):
        return ""
    acc = CFG["accounts"].get(row.get("account") or "") or {}
    conf = cm.expand(acc.get("config_dir", "~/.claude"))
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(row["cwd"]))
    p = Path(conf) / "projects" / slug / f"{row['session_id']}.jsonl"
    return str(p) if p.is_file() else ""


def next_of(cwd):
    rel = str((CFG.get("recap") or {}).get("project_log") or "").strip()
    if not rel or not cwd:
        return ""
    try:
        lines = [l for l in (Path(cwd) / rel).read_text().splitlines() if l.startswith("- ")]
    except OSError:
        return ""
    if not lines:
        return ""
    m = re.search(r"(?:prossimo|next): (.+)$", lines[-1])
    if m:
        return m.group(1).strip()
    # senza «prossimo»: l'ultima riga del recap, senza la data
    return re.sub(r"^- \d{4}-\d{2}-\d{2}: ", "", lines[-1]).strip()


def question_of(name):
    """(domanda, [etichette]) dallo schermo della sessione, via `answer NOME --show`."""
    try:
        rc, out = run_cm("answer", name, "--show")
    except subprocess.TimeoutExpired:
        return "", []
    if rc != 0:
        return "", []
    q, opts = "", []
    for l in out.splitlines():
        m = re.match(r"^\s*(?:❯)?\s*(\d+)\.\s+(.*\S)\s*$", l)
        if m:
            opts.append(m.group(2).strip())
        elif " — " in l and not q:
            q = l.split(" — ", 1)[1].strip()
            if ": " in q:
                q = q.split(": ", 1)[1].strip()
    return q, opts


def rows_live(with_questions=True):
    try:
        rc, out = run_cm("sessions", "--json")
        rows = json.loads(out) if rc == 0 and out.strip().startswith("[") else []
    except (ValueError, subprocess.TimeoutExpired):
        rows = []
    led = ledger_rows()
    for r in rows:
        st = stops_of(r.get("session_id") or "", led)
        r["last_ts"], r["last"] = (st[-1][0], st[-1][1]) if st else (None, "")
        r["tail"] = ""
        if with_questions and ui.state_of(r) == "waiting":
            r["question"], r["options"] = question_of(r.get("tmux") or r.get("name"))
    return rows


def rows_dead(live):
    """Le sessioni della fotografia «ultimo insieme buono» che non sono vive: ✗."""
    try:
        rc, out = run_cm("registry", "--good")
        d = json.loads(out) if rc == 0 and out.strip().startswith("{") else {}
    except (ValueError, subprocess.TimeoutExpired):
        d = {}
    vive = {r.get("tmux") for r in live}
    # una cartella sparita non e' una sessione da rimpiangere (residui di prove, cartelle temporanee)
    return [{"tmux": s["nome"], "name": s["nome"], "status": "dead", "cwd": s.get("cartella", ""), "account": s.get("account", ""),
             "waiting": False, "visto": s.get("visto", "")} for s in d.get("sessioni", [])
            if s.get("nome") and s["nome"] not in vive and os.path.isdir(s.get("cartella") or "")]


def all_rows():
    live = rows_live()
    return ui.order_rows(live + rows_dead(live))


# ------------------------------------------------------------------ resa
def render_list(cs):
    rows = all_rows()
    now = time.time()
    ic = icons_for(rows)
    # una riga di testa e SOLI bottoni, uno per sessione (Franz 18:28); i numeri restano accettati
    # come ripiego nell'ordine dei bottoni, il nome dettato apre la scheda
    cs.update(level="list", session="", until=now + STATE_TTL_S, list=[r["tmux"] for r in rows], options=[])
    if not rows:
        return M("bot.no_sessions"), ui.keyboard_fixed()
    master_nome = CFG["workspace"]["root_session_name"]
    alive = any(r.get("tmux") == master_nome and ui.state_of(r) != "dead" for r in rows)
    return ui.list_header(rows), ui.keyboard_list(ui.list_labels(rows, prefixes(), icons=ic), master_alive=alive)


def render_card(cs, name):
    rows = all_rows()
    row = next((r for r in rows if r.get("tmux") == name or r.get("name") == name), None)
    if not row:
        return M("bot.unknown_session", name=name), ui.keyboard_fixed(), False
    now = time.time()
    if row.get("visto") and not row.get("visto_ts"):
        row["visto_ts"] = _epoch(row["visto"])
    q, opts = row.get("question", ""), row.get("options", [])
    lines = ui.card_lines(row, esito=ui.esito_of(row.get("last") or "", row.get("tail") or ""),
                          next_step=next_of(row.get("cwd")), question=q, options=opts, prefixes=prefixes(), now=now,
                          icon=icon_of(row["tmux"]), max_lines=int(B.get("card_lines") or 20))
    cs.update(level="card", session=row["tmux"], until=now + STATE_TTL_S, options=list(opts))
    kb = ui.keyboard_card(opts, following=row["tmux"] in cs.get("follow", []), state=ui.state_of(row),
                          has_checkpoint=row["tmux"] in (cs.get("checkpoints") or {}),
                          full_question=bool(q) and bool((cs.get("qfull") or {}).get(row["tmux"])))
    return "\n".join(lines), kb, bool(q)


def quota_lines():
    try:
        rc, out = run_cm("quota", "--json")
        d = json.loads(out) if rc == 0 and out.strip().startswith("{") else {}
    except (ValueError, subprocess.TimeoutExpired):
        d = {}
    lines = [M("bot.quota_head")] if d else []
    for acc, q in d.items():
        q = q or {}
        h5 = q.get("cinque_ore_pct", q.get("five_hour_used_pct"))
        wk = q.get("settimana_pct", q.get("weekly_used_pct"))
        f = lambda v: f"{round(float(v))}%" if v is not None else "-"  # noqa: E731
        lines.append(ui.line(f"{acc} {f(h5)} {f(wk)}"))
    return lines or [M("bot.no_output")]


def help_lines():
    return [ui.line(x) for x in M("bot.help_wrist").split("|")]


# ------------------------------------------------------------------ azioni
def checkpoint(cwd):
    """Foto del workspace prima di un «sì» dal polso (idea 7): `git stash create` da' un commit con lo
    stato dei file tracciati SENZA toccare l'albero; con l'albero pulito vale HEAD. None fuori da git."""
    if not cwd or not os.path.isdir(os.path.join(cwd, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", cwd, "stash", "create"], capture_output=True, text=True, timeout=30)
        sha = r.stdout.strip()
        if not sha:
            sha = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip()
        return sha or None
    except (OSError, subprocess.SubprocessError):
        return None


def rollback(ck):
    """Riporta i file TRACCIATI allo stato del checkpoint (i nuovi non tracciati restano)."""
    try:
        r = subprocess.run(["git", "-C", ck["cwd"], "checkout", ck["sha"], "--", "."], capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def do_master():
    root = cm.expand(CFG["workspace"]["root"])
    row = alive_at(root)
    if row:
        return already_alive(row)
    rc, out = run_cm("launch", root, "--no-window")
    return (M("bot.launched", dir=root) + "\n" + out) if rc == 0 else (M("bot.launch_failed", dir=root) + "\n" + out)


def do_launch(fragment):
    if not fragment.strip():
        return M("bot.launch_usage")
    hits = resolve(fragment)
    if len(hits) == 1:
        row = alive_at(hits[0])
        if row:
            return already_alive(row)
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


def do_answer(cs, name, n):
    """Risposta a una domanda dal watch: checkpoint del workspace, poi `answer NOME N`. Torna il testo."""
    return answer_now(cs, name, n)["text"]


def answer_now(cs, name, n):
    """Risposta a una domanda dal watch: checkpoint del workspace, `answer NOME N`, poi la sessione riparte e
    il watch la segue con un messaggio vivo come dopo un prompt (12/09/2026: stesso feedback del desktop)."""
    row = next((r for r in rows_live(with_questions=False) if r.get("tmux") == name), None)
    sha = checkpoint((row or {}).get("cwd"))
    if sha:
        cs.setdefault("checkpoints", {})[name] = {"sha": sha, "cwd": row["cwd"], "ts": time.time()}
    label = label_of(name)
    try:
        rc, out = run_cm("answer", name, str(n))
    except subprocess.TimeoutExpired:
        return {"text": M("bot.timeout"), "markup": ui.keyboard_back(name, label)}
    first = (out.splitlines() or [""])[0]
    if rc != 0:
        return {"text": ui.line(out), "markup": ui.keyboard_back(name, label)}
    opts = cs.get("options") or []
    echo = f"{n} {opts[n - 1]}" if 1 <= n <= len(opts) else str(n)
    start_live(cs, name, row, echo, kind="answer")
    return {"text": ui.join(M("bot.live_sent", name=label), first),
            "markup": ui.keyboard_live(name, label), "live": name, "silent": True, "reply_to": cs.get("qmsg")}


def send_now(cs, name, text, force=False):
    """Un prompt dal watch alla sessione con `talk` (canale nativo se e' nel registro peer), dopo il
    checkpoint git del suo workspace; la sessione diventa seguita e «in attesa risposta». Niente anteprima:
    la tastiera di Wear OS conferma gia' l'invio (Franz 21:05). La risposta e' il messaggio VIVO «📤 nome» +
    l'eco del prompt, che poi si aggiorna da solo (12/09/2026)."""
    row = next((r for r in rows_live(with_questions=False) if r.get("tmux") == name), None)
    label = label_of(name)
    if not row:
        return {"text": M("bot.not_alive", name=label), "markup": ui.keyboard_back()}
    sha = checkpoint(row.get("cwd"))
    if sha:
        cs.setdefault("checkpoints", {})[name] = {"sha": sha, "cwd": row["cwd"], "ts": time.time()}
    args = ["talk", name, M("bot.prompt_prefix") + " " + text, "--no-wait"] + (["--force"] if force else [])
    try:
        rc, out = run_cm(*args)
    except subprocess.TimeoutExpired:
        return {"text": M("bot.timeout")}
    if rc != 0:
        return {"text": ui.line(out), "markup": ui.keyboard_back(name, label)}
    start_live(cs, name, row, text)
    return {"text": ui.join(M("bot.live_sent", name=label), f"«{ui.line(text)}»"),
            "markup": ui.keyboard_live(name, label), "live": name, "silent": True, "reply_to": None}


def start_live(cs, name, row, prompt, kind="prompt"):
    """Il messaggio vivo di un prompt (o di una risposta a domanda): da dove leggere il transcript, se la
    sessione era gia' occupata all'invio (allora il PRIMO Stop e' il turno precedente, non la risposta), e
    l'attesa della risposta (mark_awaiting). Il message_id arriva dal dispatch, dopo l'invio."""
    path = transcript_of(row)
    try:
        offset = os.path.getsize(path) if path else 0
    except OSError:
        offset = 0
    cs.setdefault("live", {})[name] = {
        "prompt": prompt, "kind": kind, "sent_ts": time.time(), "busy_at_send": ui.state_of(row) == "busy" if row else False,
        # senza transcript non si puo' sapere se e' arrivato: si assume di si' (ripiego a state_of); la
        # risposta a una domanda non passa dal testo del transcript
        "received": not path or kind == "answer", "transcript": path, "offset": offset,
        "mid": None, "text": "", "tool": "", "note": "", "last_edit": 0, "skipped": False, "warned": False, "long_warned": False}
    mark_awaiting(cs, name)


def finish_live(chat, cs, name, text):
    """Chiude il messaggio vivo con `text` (senza tastiera) e lo dimentica."""
    lv = (cs.get("live") or {}).pop(name, None)
    if lv and lv.get("mid"):
        edit(chat, lv["mid"], ui.line(text))


def do_stop(cs, name):
    """«Ferma»: Esc nel riquadro tmux della sessione, come sul desktop; il messaggio vivo si chiude «⏹»."""
    label = label_of(name)
    try:
        sess = _load("cm-sessions")
        r = subprocess.run(sess.TMUX + ["send-keys", "-t", name, "Escape"], capture_output=True, text=True, timeout=5)
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        return {"text": ui.join(*M("bot.stop_failed", name=label).split("|")), "markup": ui.keyboard_back(name, label)}
    (cs.get("awaiting") or {}).pop(name, None)
    return {"text": ui.line(M("bot.live_stopped", name=label)), "markup": ui.keyboard_back(name, label), "stopped": name}


def mark_awaiting(cs, name):
    """Un prompt del polso aspetta la risposta: al primo Stop della sessione torna a prescindere dalla
    durata del turno (19:20: la risposta a «Riassumi in due righe» non tornava). Da li' la sessione e'
    anche seguita."""
    cs.setdefault("awaiting", {})[name] = time.time()
    if name not in (cs.get("follow") or []):
        cs.setdefault("follow", []).append(name)
    seen = cs.setdefault("seen", {})
    seen[name] = max(float(seen.get(name) or 0), time.time())   # mai indietro: uno stop gia' annunciato resta tale


def answer_text(text, cap=1200):
    """L'esito o la risposta per il polso e il telefono, LEGGIBILE (Franz 22:49: niente righe strette):
    testo pulito dal markdown, la riga «Esito:» per prima, poi la coda del messaggio, senza a-capo forzati
    (Telegram va a capo da solo); oltre `cap` caratteri si taglia e si offre «Tutto». Torna (testo, tagliato)."""
    righe = [ui.strip_markdown(l) for l in str(text or "").splitlines()]
    righe = [l for l in righe if l.strip()]
    esito = next((l for l in reversed(righe) if l.lower().startswith("esito")), "")
    seen = set()
    resto = [l for l in righe if l != esito and not (l in seen or seen.add(l))]
    if esito:
        # una frase sola, ≤ 120, per la lettura vocale (via master 12/09)
        m = re.match(r"^(\s*esito\s*:\s*)(.*)$", esito, flags=re.I)
        voice, leftover = ui.voice_split(m.group(2) if m else esito)
        esito = "Esito: " + voice
        if leftover:
            resto.insert(0, leftover)
    parts = ([esito] if esito else []) + resto
    full = "\n".join(parts)
    if len(full) <= cap:
        return full, False
    if esito and len(esito) < cap:
        room = cap - len(esito) - 2
        return esito + "\n…" + "\n".join(resto)[-room:], True
    return full[:cap - 1] + "…", True


def answer_lines(text, width=ui.WIDTH, max_lines=ui.MAX_LINES - 1):
    """La risposta pulita per il polso: la riga «Esito:» per prima se c'e', poi le ULTIME righe; torna
    (righe, troncato)."""
    righe = [ui.strip_markdown(l) for l in str(text or "").splitlines()]
    righe = [l for l in righe if l.strip()]
    esito = next((l for l in reversed(righe) if l.lower().startswith("esito")), "")
    seen = set()
    resto = [l for l in righe if l != esito and not (l in seen or seen.add(l))]   # niente doppioni (tail ⊇ esito)
    out_e = ui.wrap(esito, width, 6) if esito else []
    out_r = []
    for l in resto:
        out_r += ui.wrap(l, width, 60)   # tutto: il taglio lo decide il totale, non la singola riga
    if len(out_e) + len(out_r) <= max_lines:
        return out_e + out_r, False
    room = max(0, max_lines - len(out_e))
    return out_e + (out_r[-room:] if room else []), True


def toggle_follow(cs, name):
    fl = cs.setdefault("follow", [])
    if name in fl:
        fl.remove(name)
        return M("bot.unfollowed", name=label_of(name))
    fl.append(name)
    cs.setdefault("seen", {})[name] = time.time()
    return M("bot.followed", name=label_of(name))


def handle(text, cs=None, cq_data=None):
    """Un messaggio (o un tap: cq_data) → {"text", "markup", "silent", "reply_to", "question", "live", "stopped"}.
    Senza `cs` (compatibilita') si crea uno stato usa-e-getta."""
    cs = cs if cs is not None else chat_state({}, 0)
    now = time.time()
    # la scheda non scade finche' si aspetta la risposta della sua sessione (12/09: «scrivo e non parte»)
    expired = now > float(cs.get("until") or 0) and not (cs.get("session") and cs["session"] in (cs.get("awaiting") or {}))
    ctx = cs.get("session") if cs.get("level") == "card" and not expired and cs.get("session") else ""
    if cq_data:
        if cq_data.startswith("ans:"):
            _, name, n = cq_data.split(":", 2)
            cs.update(level="card", session=name, until=now + STATE_TTL_S)
            return answer_now(cs, name, int(n))
        if cq_data.startswith("card:"):
            name = cq_data[5:]
            t, m, q = render_card(cs, name)
            return {"text": t, "markup": m, "silent": True, "reply_to": None, "question": q}
        if cq_data.startswith("follow:") or cq_data.startswith("unfollow:"):
            action, name = cq_data.split(":", 1)
            fl = cs.setdefault("follow", [])
            if action == "unfollow" and name in fl:
                fl.remove(name); txt = M("bot.unfollowed", name=label_of(name))
            elif action == "follow" and name not in fl:
                txt = toggle_follow(cs, name)
            else:
                txt = M("bot.unfollowed" if action == "unfollow" else "bot.followed", name=label_of(name))
            return {"text": txt, "markup": ui.keyboard_back(name, label_of(name)), "silent": True, "reply_to": None}
        if cq_data.startswith("full:"):
            name = cq_data[5:]
            full = (cs.get("full") or {}).get(name) or ""
            return {"text": full[:MAX_TEXT] if full else M("bot.no_output"), "markup": ui.keyboard_back(name, label_of(name)), "silent": True, "reply_to": None}
        if cq_data.startswith("q:"):
            name = cq_data[2:] or cs.get("session") or ""
            full = (cs.get("qfull") or {}).get(name) or ""
            return {"text": full[:MAX_TEXT] if full else M("bot.no_output"), "markup": ui.keyboard_back(name, label_of(name)) if name else ui.keyboard_back(), "silent": True, "reply_to": cs.get("qmsg")}
        if cq_data.startswith("stop:"):
            out = do_stop(cs, cq_data[5:]); out.update(silent=True, reply_to=None); return out
        if cq_data.startswith("screen:"):
            cmd, rest = "screen", cq_data[7:]
        elif cq_data.startswith("retry:"):
            name = cq_data[6:]
            lv = (cs.get("live") or {}).get(name) or {}
            if not lv.get("prompt") or lv.get("kind") == "answer":
                return {"text": M("bot.retry_nothing"), "markup": ui.keyboard_back(name, label_of(name)), "silent": True, "reply_to": None}
            out = {"silent": True, "reply_to": None}; out.update(send_now(cs, name, lv["prompt"], force=True)); return out
        elif cq_data.startswith("n:"):
            cmd, rest = "number", int(cq_data[2:])
            cs["level"] = "list"
        elif cq_data.startswith("opt:"):
            cmd, rest = "number", int(cq_data[4:])
            expired = False
        else:
            cmd, rest = cq_data, ""
    else:
        cmd, rest = ui.parse_command(text)
    out = {"markup": ui.keyboard_back(ctx, label_of(ctx) if ctx else ""), "silent": True, "reply_to": None, "question": False}
    if cmd == "start":
        out["text"] = M("bot.start")
        out["markup"] = ui.keyboard_start(master_alive=alive_at(cm.expand(CFG["workspace"]["root"])) is not None)
    elif cmd == "text" and rest:
        raw = " ".join(str(text or "").split())
        in_card = bool(ctx)
        opts = [o.lower() for o in (cs.get("options") or [])]
        single = len(raw.split()) == 1
        found = ui.match_session(raw, [r["tmux"] for r in all_rows()], prefixes()) if single else []
        # fuori scheda, la sola sessione in attesa (o seguita) e' la destinataria ovvia del testo
        pending = list(dict.fromkeys(list((cs.get("awaiting") or {}).keys()) or list(cs.get("follow") or [])))
        if in_card and opts and raw.lower() in opts:
            # in scheda, il testo di un'opzione risponde alla domanda
            out.update(answer_now(cs, cs["session"], opts.index(raw.lower()) + 1))
        elif single and len(found) == 1:
            # una parola dettata = il nome di una sessione (o un prefisso univoco): la sua scheda
            render_list(cs)
            out["text"], out["markup"], out["question"] = render_card(cs, found[0])
        elif single and found:
            rows = [r for r in all_rows() if r.get("tmux") in found]
            cs.update(level="list", list=[r["tmux"] for r in rows], until=now + STATE_TTL_S)
            out["text"] = M("bot.choose", n=len(rows))
            out["markup"] = ui.keyboard_list(ui.list_labels(rows, prefixes(), icons=icons_for(rows)))
        elif len(raw) < 3:
            out["text"] = ""   # ignorato: niente risposta
        elif in_card:
            # PROMPT LIBERO alla sessione della scheda: parte subito (Franz 21:05)
            out.update(send_now(cs, cs["session"], raw))
        elif len(pending) == 1:
            out.update(send_now(cs, pending[0], raw))
        else:
            # nel livello elenco un testo libero NON parte: prima la sessione
            rows = all_rows()
            cs.update(level="list", list=[r["tmux"] for r in rows], until=now + STATE_TTL_S)
            out["text"] = M("bot.choose_first")
            out["markup"] = ui.keyboard_list(ui.list_labels(rows, prefixes(), icons=icons_for(rows)))
    elif cmd == "help" or cmd == "text":
        out["text"] = "\n".join(help_lines())
    elif cmd in ("sessions", "list"):
        if rest.strip() in ("full", "tutto"):
            out["text"] = do_sessions()
        else:
            out["text"], out["markup"] = render_list(cs)
    elif cmd == "master":
        out["text"] = do_master()
    elif cmd == "launch":
        out["text"] = do_launch(rest)
    elif cmd == "quota":
        out["text"] = "\n".join(quota_lines())
    elif cmd == "recap":
        d = _load("cm-recap")
        _, _, groups, label = d.build(["--full"] if rest.strip() in ("full", "tutto") else [])
        out["text"] = d.render_short(groups, label, as_html=False)
        out["silent"] = False   # chiesto apposta: deve farsi sentire
    elif cmd == "screen":
        name = str(rest).split()[0] if str(rest).strip() else cs.get("session")
        if not name:
            out["text"] = M("bot.no_current")
        else:
            # le ultime 30 righe in un blocco <pre> (idea 2 del doc ecosistema, via master 12/09): sul telefono
            # si legge intero, sul polso scorre
            import html as _html
            rc, o = run_cm("screen", name, "--lines", "30")
            body = "\n".join(o.splitlines()[-30:]).rstrip()
            out["text"] = ("<pre>" + _html.escape(body) + "</pre>") if body else M("bot.no_output")
            out["parse_mode"] = "HTML" if body else None
            out["markup"] = ui.keyboard_back(name, label_of(name))
    elif cmd == "follow":
        name = rest.split()[0] if rest.strip() else cs.get("session")
        out["text"] = toggle_follow(cs, name) if name else M("bot.no_current")
        if name:
            out["markup"] = ui.keyboard_back(name, label_of(name))
    elif cmd == "resume":
        name = rest.split()[0] if rest.strip() else cs.get("session")
        if not name:
            out["text"] = M("bot.no_current")
        else:
            row = next((r for r in rows_live(with_questions=False) if r.get("tmux") == name), None)
            rc, o = run_cm("talk", name, M("bot.prompt_prefix") + " " + str((CFG.get("guard") or {}).get("resume_prompt") or "riprendi da dove eri"), "--no-wait")
            if rc == 0:
                start_live(cs, name, row, M("bot.resumed", name=label_of(name)))
                out["markup"] = ui.keyboard_live(name, label_of(name))
                out["live"] = name
                cs.update(level="card", session=name, until=now + STATE_TTL_S)
            out["text"] = ui.join(M("bot.live_sent", name=label_of(name)), M("bot.resumed", name=label_of(name))) if rc == 0 else ui.line(o)
    elif cmd == "stop":
        name = rest.split()[0] if rest.strip() else cs.get("session")
        if name:
            out.update(do_stop(cs, name))
        else:
            out["text"] = M("bot.no_current")
    elif cmd == "retry":
        name = cs.get("session")
        lv = (cs.get("live") or {}).get(name) or {} if name else {}
        if lv.get("prompt") and lv.get("kind") != "answer":
            out.update(send_now(cs, name, lv["prompt"], force=True))
        else:
            out["text"] = M("bot.retry_nothing")
    elif cmd == "rollback":
        cks = cs.get("checkpoints") or {}
        name = cs.get("session") if cs.get("session") in cks else (max(cks, key=lambda k: cks[k]["ts"]) if cks else "")
        if not name:
            out["text"] = M("bot.no_checkpoint")
        else:
            out["text"] = M("bot.rolled_back" if rollback(cks[name]) else "bot.rollback_failed", name=ui.short_name(name, prefixes()))
            out["markup"] = ui.keyboard_back(name, label_of(name))
    elif cmd == "yes":
        if ctx and cs.get("options"):
            out.update(answer_now(cs, cs["session"], 1))
        else:
            out["text"] = "\n".join(help_lines())
    elif cmd == "number":
        n = int(rest)
        if ctx and cs.get("options") and 1 <= n <= len(cs["options"]):
            out.update(answer_now(cs, cs["session"], n))
        else:
            if not cs.get("list") or expired:
                render_list(cs)
            lst = cs.get("list") or []
            if 1 <= n <= len(lst):
                out["text"], out["markup"], out["question"] = render_card(cs, lst[n - 1])
            else:
                out["text"], out["markup"] = render_list(cs)
    else:
        out["text"] = "\n".join(help_lines())
    # «📍 nome» in testa alle risposte che non nominano la sessione: si sa sempre in che scheda si e' (12/09)
    if ctx and out.get("text") and cmd in ("quota", "help") or (ctx and cmd == "text" and not rest):
        first, _, rest = out["text"].partition("\n")
        out["text"] = ui.join(f"📍 {label_of(ctx)}", first) + ("\n" + rest if rest else "")
    return out


def remember_question(name, message_ids, options=(), full_question=""):
    """Dall'avviso dell'hook (cm-answer --notify): ogni chat entra nella scheda di quella sessione, cosi'
    un «2» dal polso risponde a LEI, come reply al messaggio dell'avviso. `message_ids`: {chat: id} o un id.
    `full_question`: il testo integrale se l'avviso l'ha sintetizzato (bottone «Domanda intera»)."""
    st = state_load()
    for chat in allowed_chats():
        cs = chat_state(st, chat)
        mid = message_ids.get(chat) if isinstance(message_ids, dict) else message_ids
        cs.update(level="card", session=name, until=time.time() + STATE_TTL_S, options=list(options), qmsg=mid)
        cs.setdefault("qfull", {})
        if full_question:
            cs["qfull"][name] = full_question
        else:
            cs["qfull"].pop(name, None)
    state_save(st)


# ------------------------------------------------------------------ sessioni seguite e digest
def check_follows(st):
    """Per ogni chat: le sessioni seguite e quelle in attesa di risposta. Uno stop dopo un turno > TURN_MIN_S
    → «✓ nome ha finito:» (notifica normale); la RISPOSTA a un prompt del watch → «✓ nome» + la riga Watch:
    (sempre, a prescindere dalla durata); sparita → «✗ nome» e non piu' seguita; StopFailure → «✗ nome errore».
    E il messaggio vivo di ogni prompt in corso si aggiorna dal transcript (live_update)."""
    chats = st.get("chats") or {}
    if not any(cs.get("follow") or cs.get("awaiting") or cs.get("live") for cs in chats.values()):
        return
    live = {r.get("tmux"): r for r in rows_live(with_questions=False)}
    led = ledger_rows()
    now = time.time()
    for chat, cs in chats.items():
        aw = cs.setdefault("awaiting", {})
        lv_all = cs.setdefault("live", {})
        for name in list(set(cs.get("follow") or []) | set(aw) | set(lv_all)):
            row = live.get(name)
            label = label_of(name)
            if not row:
                reply(chat, ui.line(f"✗ {label}"), silent=False)
                if name in (cs.get("follow") or []):
                    cs["follow"].remove(name)
                aw.pop(name, None)
                finish_live(chat, cs, name, f"✗ {label}")
                continue
            seen = float((cs.get("seen") or {}).get(name) or 0)
            since = float(aw.get(name) or 0)
            lv = lv_all.get(name) or {}
            sid = row.get("session_id") or ""
            for t, err in failures_of(sid, led):
                if t <= seen or not (name in aw or name in (cs.get("follow") or [])):
                    continue
                first = (err.splitlines() or [""])[0]
                reply(chat, ui.join(M("bot.failure_head", name=label), first),
                      reply_markup=ui.keyboard_outcome(name, label), silent=False)
                aw.pop(name, None)
                finish_live(chat, cs, name, M("bot.failure_head", name=label))
                cs.setdefault("seen", {})[name] = max(seen, t)
            for t, esito, prev, full, watch in stops_full(sid, led):
                if t <= seen:
                    continue
                followed = name in (cs.get("follow") or [])
                long_turn = prev is not None and t - prev >= TURN_MIN_S
                if name in aw and t > since:
                    # la RISPOSTA a un prompt del watch: lo Stop con la riga Watch:, oppure il primo se la
                    # sessione era ferma all'invio, oppure il secondo (il primo era il turno precedente)
                    is_answer = bool(watch) or not lv.get("busy_at_send") or lv.get("skipped")
                    if not is_answer:
                        lv["skipped"] = True
                        if followed and long_turn:
                            _send_outcome(chat, cs, name, label, full or esito)
                        cs.setdefault("seen", {})[name] = t
                        continue
                    whole = ui.strip_markdown(full or esito)
                    if watch:
                        head, body = M("bot.answer_head", name=label), watch
                        rest = [l for l in whole.splitlines() if l.strip() and ui.watch_line(l) != watch]
                        cut = bool(rest)
                    else:
                        head = M("bot.answer_head_full", name=label)
                        body, cut = answer_text(full or esito)
                    if cut:
                        cs.setdefault("full", {})[name] = whole
                    reply(chat, ui.line(head) + "\n" + body, reply_markup=ui.keyboard_outcome(name, label, cut=cut), silent=False)
                    aw.pop(name, None)
                    age = ui.age_compact(max(0, t - float(lv.get("sent_ts") or t))) if lv else ""
                    finish_live(chat, cs, name, M("bot.live_done", name=label, age=age or "0m"))
                    cs.update(level="card", session=name, until=time.time() + STATE_TTL_S, options=[])
                elif followed and long_turn:
                    # l'esito di una sessione seguita, per intero come una risposta (Franz 21:25: due righe
                    # troncate non servono): «Esito:» per prima, poi le ultime righe, «Leggi tutto» se e' lungo;
                    # e la chat resta in scheda di quella sessione, cosi' il prossimo prompt si detta subito
                    _send_outcome(chat, cs, name, label, full or esito)
                    cs.update(level="card", session=name, until=time.time() + STATE_TTL_S, options=[])
                cs.setdefault("seen", {})[name] = t
            if name in lv_all and name in aw:
                live_update(chat, cs, name, row, now)


def _send_outcome(chat, cs, name, label, text):
    body, cut = answer_text(text)
    if cut:
        cs.setdefault("full", {})[name] = ui.strip_markdown(text)
    reply(chat, M("bot.outcome_head", name=label) + "\n" + body, reply_markup=ui.keyboard_outcome(name, label, cut=cut), silent=False)


def live_update(chat, cs, name, row, now):
    """Il messaggio vivo: dal transcript (nuovi eventi dall'offset) la presa in carico, il tool in corso e
    l'ultimo testo; un edit ogni LIVE_EDIT_S al massimo, solo se cambia; «non ha ricevuto» dopo
    RECEIVE_TIMEOUT_S senza traccia del prompt; un solo «ancora al lavoro» dopo ANSWER_TIMEOUT_S."""
    lv = cs["live"][name]
    if not lv.get("mid"):
        return
    label = label_of(name)
    since = now - float(lv.get("sent_ts") or now)
    if lv.get("transcript"):
        events, lv["offset"] = ui.transcript_events(lv["transcript"], int(lv.get("offset") or 0))
        for ev in events:
            if ev[0] == "user" and (WATCH_MARK in ev[1] or (lv.get("prompt") or "")[:40] in ev[1]):
                lv["received"] = True; lv["tool"] = ""; lv["note"] = ""
            elif ev[0] == "tool":
                lv["tool"] = ui.tool_line(ev[1], ev[2]); lv["note"] = ""
            elif ev[0] == "text":
                lv["note"] = ev[1]
    st = ui.state_of(row)
    if not lv.get("received"):
        if since >= RECEIVE_TIMEOUT_S and not lv.get("warned"):
            lv["warned"] = True
            text = ui.join(*M("bot.not_received", name=label).split("|"))
            edit(chat, lv["mid"], text, ui.keyboard_retry(name, label)); lv["text"] = text
        return
    if st == "waiting":
        text = ui.line(M("bot.live_waiting", name=label))
    else:
        text = "\n".join(ui.live_lines(label, since, lv.get("tool") or "", lv.get("note") or ""))
    if text != lv.get("text") and (not lv.get("text") or now - float(lv.get("last_edit") or 0) >= LIVE_EDIT_S):
        if edit(chat, lv["mid"], text, ui.keyboard_live(name, label)):
            lv["text"] = text; lv["last_edit"] = now
    if since >= ANSWER_TIMEOUT_S and not lv.get("long_warned"):
        lv["long_warned"] = True
        reply(chat, ui.line(M("bot.still_working", name=label)), reply_markup=ui.keyboard_live(name, label), silent=True)


def digest():
    """«Cosa aspetta te», le 8:00: domande pendenti, ferme da ieri, sparite (✗)."""
    if not token():
        return 1
    rows = all_rows()
    import datetime as _dt
    midnight = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    lines = []
    for r in rows:
        st = ui.state_of(r)
        if st == "waiting":
            lines.append(ui.join(f"❓ {label_of(r['tmux'])}", ui.question_gist(str(r.get('question') or '')) or str(r.get('question') or '')))
    for r in rows:
        if ui.state_of(r) == "idle" and r.get("last_ts") and r["last_ts"] < midnight:
            lines.append(ui.join(f"✓ {label_of(r['tmux'])}", f"ferma {ui.age_compact(time.time() - r['last_ts'])}"))
    for r in rows:
        if ui.state_of(r) == "dead":
            lines.append(ui.line(f"✗ {label_of(r['tmux'])}"))
    if not lines:
        lines = [M("bot.digest_empty")]
    lines = lines[:ui.MAX_LINES]
    waiting = [r["tmux"] for r in rows if ui.state_of(r) == "waiting"]
    for c in sorted(allowed_chats()):
        reply(c, "\n".join(lines), reply_markup=ui.keyboard_digest(waiting, label_of), silent=True)
    return 0


# ------------------------------------------------------------------ dispatch (poll e serve)
def dispatch(updates, off_p, st):
    """Ogni update autorizzato → handle → risposta; l'offset si conferma DOPO il dispatch di ciascuno
    (un crash a meta' non perde e non ripete)."""
    allowed = allowed_chats()
    served = 0
    for u in updates:
        uid = u.get("update_id", 0)
        cq = u.get("callback_query") or {}
        msg = cq.get("message") if cq else (u.get("message") or {})
        msg = msg or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        frm = ((cq.get("from") if cq else msg.get("from")) or {}).get("id")
        text = str(cq.get("data") or "") if cq else (msg.get("text") or "")
        authorized = chat.get("type") == "private" and (str(chat_id) in allowed or str(frm) in allowed)
        if not authorized:
            log(f"ignored update {uid}: chat {chat_id} ({chat.get('type')}) not allowed")
        elif not text.strip():
            log(f"ignored update {uid}: no text from {chat_id}")
        else:
            head = text.strip().split()[0] if text.strip() else ""
            log(f"update {uid} from {chat_id}: {'tap ' if cq else ''}{head[:40]}")
            if cq:
                try:
                    api("answerCallbackQuery", callback_query_id=cq.get("id"))
                except (urllib.error.URLError, OSError, ValueError) as e:
                    log(f"answerCallbackQuery FAILED: {e}")
            cs = chat_state(st, chat_id)
            # il recap costa decine di secondi (la frase del modello per progetto): un cenno subito,
            # altrimenti dal polso sembra morto (20:27: 54 s di silenzio)
            if (text == "recap" if cq else ui.parse_command(text)[0] == "recap"):
                reply(chat_id, M("bot.recap_wait"), silent=True)
            try:
                res = handle(text, cs, cq_data=text if cq else None)
            except subprocess.TimeoutExpired:
                res = {"text": M("bot.timeout"), "markup": ui.keyboard_fixed(), "silent": True, "reply_to": None}
            log(f"  → {res['text'].splitlines()[0][:120] if res.get('text') else '(ignorato)'}")
            if res.get("stopped"):
                finish_live(chat_id, cs, res["stopped"], M("bot.live_stopped", name=label_of(res["stopped"])))
            if res.get("text"):
                mid = reply(chat_id, res["text"], parse_mode=res.get("parse_mode"), reply_markup=res.get("markup"), silent=res.get("silent", True), reply_to=res.get("reply_to"))
                if res.get("question") and mid:
                    cs["qmsg"] = mid
                if res.get("live") and mid and res["live"] in (cs.get("live") or {}):
                    cs["live"][res["live"]]["mid"] = mid
            state_save(st)
            served += 1
        off_p.write_text(str(uid + 1))
    return served


# ------------------------------------------------------------------ serve (long polling)
def serve_pid_path():
    return Path(cm.expand(CFG["state_dir"])) / "bot-serve.pid"


def serve_status_path():
    return Path(cm.expand(CFG["state_dir"])) / "bot-serve.json"


def serve_alive():
    try:
        pid = int(serve_pid_path().read_text().strip() or 0)
        if pid > 0:
            os.kill(pid, 0)
            return pid
    except (OSError, ValueError):
        pass
    return 0


def serve():
    """Il daemon: un ciclo di getUpdates lungo (Telegram tiene la connessione e risponde appena arriva
    qualcosa), stesso offset e stesso dispatch di `poll`; riconnessione a scalare su errori di rete;
    un solo consumatore del token (lock + plugin telegram)."""
    if not B["enabled"]:
        print(M("bot.disabled")); return 2
    if not token():
        print(M("bot.no_token", path=B["token_file"])); return 1
    pid = plugin_poller_pid()
    if pid:
        print(M("bot.serve_plugin", pid=pid)); log(f"serve: plugin telegram vivo (pid {pid}), esco"); return 3
    off_p = offset_path()
    off_p.parent.mkdir(parents=True, exist_ok=True)
    lock = open(str(off_p) + ".serve.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(M("bot.serve_locked")); log("serve: lock preso da un altro serve, esco"); return 4
    serve_pid_path().write_text(str(os.getpid()))
    started = time.time()
    stop = {"now": False}
    import signal

    def _stop(*_):
        # una bandiera non basta: urlopen sta dentro un recv che Python riprende dopo il segnale (PEP 475);
        # l'eccezione lo interrompe e il finally chiude pulito
        stop["now"] = True
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log(f"serve: avvio pid {os.getpid()}")
    st_ = {"pid": os.getpid(), "started": started, "last_update_ts": None, "last_loop_ms": None, "reconnects": 0, "served": 0}
    tries = 0
    idle = float(os.environ.get("CM_BOT_SERVE_IDLE_S", "0") or 0)
    try:
        first = not off_p.is_file()
        while not stop["now"]:
            offset = None
            if off_p.is_file():
                try:
                    offset = int(off_p.read_text().strip() or 0) or None
                except ValueError:
                    offset = None
            t0 = time.time()
            try:
                # il long poll tiene la connessione fino a serve_timeout_s: il timeout HTTP deve superarlo
                # (dal vivo, 18:40: con i 20 s di http_timeout_s ogni giro era «rete assente»)
                lungo = 0 if first else float(B.get("serve_timeout_s") or 50)
                # con un messaggio vivo il giro si accorcia alla cadenza degli edit (il transcript va riletto)
                if not first and any(cs.get("live") for cs in (state_load().get("chats") or {}).values()):
                    lungo = min(lungo, max(1.0, LIVE_EDIT_S))
                r = api("getUpdates", _http_timeout=lungo + 10, offset=offset, timeout=int(lungo),
                        allowed_updates='["message","callback_query"]')
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    log("serve: 409 conflict, un altro consumatore tiene il token: esco"); return 3
                delay = SERVE_BACKOFF[min(tries, len(SERVE_BACKOFF) - 1)]; tries += 1; st_["reconnects"] += 1
                log(f"serve: HTTP {e.code}, riconnessione fra {delay:g}s"); time.sleep(delay); continue
            except (urllib.error.URLError, OSError, ValueError) as e:
                delay = SERVE_BACKOFF[min(tries, len(SERVE_BACKOFF) - 1)]; tries += 1; st_["reconnects"] += 1
                log(f"serve: rete assente ({str(e)[:80]}), riconnessione fra {delay:g}s"); time.sleep(delay); continue
            if tries:
                log("serve: riconnesso")
            tries = 0
            updates = r.get("result", []) if isinstance(r, dict) else []
            if first:
                fresh = time.time() - int(B["first_run_max_age_s"])
                old = [u for u in updates if int((u.get("message") or {}).get("date") or 0) < fresh]
                updates = [u for u in updates if u not in old]
                last = max((u.get("update_id", 0) for u in old), default=0)
                if not updates:
                    off_p.write_text(str(last + 1 if old else 0))
                log(f"serve: primo giro, {len(old)} vecchi scartati, {len(updates)} recenti")
                first = False
            st = state_load()
            n = dispatch(updates, off_p, st)
            if updates:
                st_["last_update_ts"] = time.time(); st_["served"] += n
            try:
                check_follows(st); state_save(st)
            except subprocess.TimeoutExpired:
                log("check_follows: timeout")
            st_["last_loop_ms"] = int((time.time() - t0) * 1000)
            try:
                serve_status_path().write_text(json.dumps(st_))
            except OSError:
                pass
            if not updates and idle:
                time.sleep(idle)
    finally:
        log("serve: stop")
        try:
            serve_pid_path().unlink()
        except OSError:
            pass
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()
    return 0


def ensure():
    """Dal cron (e dal restore): `serve` vivo → niente; morto → rilanciato staccato, log in bot.log."""
    if not B["enabled"]:
        return 0
    pid = serve_alive()
    if pid:
        print(M("bot.serve_alive", pid=pid)); return 0
    lp = log_path(); lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a") as out:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"], stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True, env=os.environ.copy())
    for _ in range(20):
        time.sleep(0.1)
        if serve_alive():
            break
    print(M("bot.serve_started", pid=serve_alive()))
    return 0


def serve_stop():
    pid = serve_alive()
    if not pid:
        return False
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(30):
        time.sleep(0.1)
        if not serve_alive():
            break
    return True


# ------------------------------------------------------------------ poll
def poll():
    if not B["enabled"]:
        return 0
    if serve_alive():
        print(M("bot.serve_alive", pid=serve_alive())); return 0   # il daemon ascolta: il giro manuale non serve
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
            r = api("getUpdates", offset=offset, timeout=0, allowed_updates='["message","callback_query"]')
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
        st = state_load()
        dispatch(updates, off_p, st)
        try:
            check_follows(st)
            state_save(st)
        except subprocess.TimeoutExpired:
            log("check_follows: timeout")
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
    return f"{'* ' if m == 1 else f'*/{m} '}* * * * {shim} bot ensure >/dev/null 2>&1"


def digest_line():
    shim = cm.home() / ".local" / "bin" / "claude-master"
    hh, mm = (str(B.get("digest_time") or "08:00").split(":") + ["0"])[:2]
    return f"{int(mm)} {int(hh)} * * * {shim} bot digest >/dev/null 2>&1"


def set_my_commands():
    """Il menu comandi di Telegram (per il telefono): le stesse voci delle parole nude."""
    cmds = [{"command": "sessions", "description": "elenco (s, sessioni)"}, {"command": "master", "description": "sessione della radice (m)"},
            {"command": "launch", "description": "lancia <frammento> (l)"}, {"command": "quota", "description": "quota (q)"},
            {"command": "recap", "description": "recap di oggi"}, {"command": "help", "description": "aiuto (?)"}]
    try:
        api("setMyCommands", commands=json.dumps(cmds, ensure_ascii=False))
        return True
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"setMyCommands FAILED: {e}")
        return False


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
    set_my_commands()
    cur = crontab_read()
    if "claude-master bot ensure" in cur and "claude-master bot digest" in cur:
        print(M("bot.cron_present"))
        ensure()
        return 0
    lines = [l for l in cur.splitlines() if "claude-master bot" not in l]
    lines += ["# claude-master bot: il telefono e l'orologio (poll ogni minuto, digest del mattino)", cron_line(), digest_line()]
    crontab_write("\n".join(lines) + "\n")
    print(M("bot.cron_installed", line=cron_line() + " · " + digest_line()))
    ensure()
    return 0


def uninstall():
    if serve_stop():
        print(M("bot.serve_stopped"))
    cur = crontab_read()
    if "claude-master bot" not in cur:
        print(M("bot.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "claude-master bot" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("bot.cron_removed"))
    return 0


def status():
    print(M("bot.status_enabled", state="true" if B["enabled"] else "false"))
    print(M("bot.status_cron", state="yes" if "claude-master bot ensure" in crontab_read() else "no", line=cron_line()))
    pid = serve_alive()
    if pid:
        try:
            sj = json.loads(serve_status_path().read_text())
        except (OSError, ValueError):
            sj = {}
        up = int(time.time() - float(sj.get("started") or time.time()))
        lu = sj.get("last_update_ts")
        print(M("bot.status_serve", pid=pid, up=f"{up // 3600}h{(up % 3600) // 60:02d}m", last=time.strftime("%H:%M:%S", time.localtime(lu)) if lu else "-",
                ms=sj.get("last_loop_ms") if sj.get("last_loop_ms") is not None else "-", rec=sj.get("reconnects", 0)))
    else:
        print(M("bot.status_serve_dead"))
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
    if verb == "digest":
        return digest()
    if verb == "serve":
        return serve()
    if verb == "ensure":
        return ensure()
    if verb == "resolve":   # solo per le prove e la curiosità: i candidati di un frammento
        for h in resolve(" ".join(argv[1:])):
            print(h)
        return 0
    print(M("bot.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

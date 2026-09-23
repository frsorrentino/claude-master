#!/usr/bin/env python3
"""Le funzioni PURE di cm-relay (fase 1 dell'app polso, design 12/09/2026 §1): da sorgenti gia' raccolte a
`/state` nella forma del contratto v1 (claude-master-watch/contract/state-*.json, copiato in
tests/fixtures/relay/), il diff fra due stati → `/events`, il tier di una domanda, il limite di 8 KB.
Niente I/O qui: cm-relay.py raccoglie e scrive, questo modulo costruisce. Testato in tests/relay-verify.py (R2, R3).

`src` (dict) per build_state:
  host, root (radice dei workspace), prefixes (prefissi tmux degli account), high_words
  rows: le sessioni (vive da `sessions --json` + sparite dalla fotografia): name, tmux, account, cwd, status
        (busy|idle|waiting|dead), waiting, session_id, link, attached, started_at (ms), visto_ts
  ledger: le righe del ledger (event, session_id, ts iso, last/tail/esito/watch)
  questions: {tmux: {kind, tool, text, options[etichette], asked_at}}
  quota: {account: {cinque_ore_pct, settimana_pct, reset_settimanale, vecchia}}
  projects: [{path, name, account}] · night: {queued, running} · recap: {date, items[{project, done, next}]}
  follow: nomi seguiti · awaiting: nomi in attesa di risposta a un prompt dal watch

Tempi di una sessione in `/state` (chiesto dall'app il 13/09/2026, qui perche' non si reinterpreti):
  `since`        la NASCITA della sessione (startedAt del registro) mentre lavora o e' ferma; l'istante della
                 domanda se aspetta; l'ultimo avvistamento se e' sparita. Non cambia a ogni cambio di stato.
  `turn_started` l'ultimo prompt (o ripresa), valorizzato SOLO mentre lo stato e' busy o awaiting: a turno
                 finito torna null.
  `outcome.at`   l'ultimo Stop: e' questo il movimento di una sessione ferma, e si aggiorna a ogni fine turno
                 perche' l'hook scrive una riga nel ledger ogni volta.
  Chi vuole «l'ultimo movimento» usa max(since, turn_started, outcome.at): non serve un campo in piu'.
  next: {tmux: «→ prossimo» dal recap del progetto} · tools: {tmux: tool in corso}
"""
import datetime as _dt
import json
import re

V = 1
ORDER = {"waiting": 0, "busy": 1, "awaiting": 1, "idle": 2, "gone": 3}
SHORT_MAX = 200   # 1.6: la riga «Watch:»/«Esito:» intera (a 60 il polso mostrava mezza frase; chiesto dall'app il 14/09)
KEEP_GONE = 3     # fit_state: le sessioni finite piu' recenti che restano quando lo stato non entra
RECAP_CUT = 80    # 1.7 fit_state: `done` e `next` del recap accorciati a fine parola invece di sparire per primi
HIGH_WORDS = ["rm -rf", "git push", "deploy", "DROP", "ssh", "sudo", "--force", "git reset --hard"]
LOW_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch", "LS", "TodoWrite"}
STATE_ICON = {"waiting": "❓", "busy": "▶", "awaiting": "▶", "idle": "✓", "gone": "✗"}
# contratto 1.1 (l'utente via master 12/09 16:27): il badge dell'orologio = forma dall'account, colore = quello della scheda
# del Terminale (cm-color), a prescindere dalla forma o dal cuore dell'emoji
COLORS = {"🟠": "#F5A623", "🟧": "#F5A623", "🧡": "#F5A623", "🟡": "#F4D03F", "🟨": "#F4D03F", "💛": "#F4D03F",
          "🔴": "#E74C3C", "🟥": "#E74C3C", "❤️": "#E74C3C", "❤": "#E74C3C", "🟢": "#2ECC71", "🟩": "#2ECC71", "💚": "#2ECC71",
          "🔵": "#3B82F6", "🟦": "#3B82F6", "💙": "#3B82F6", "🟣": "#9B59B6", "🟪": "#9B59B6", "💜": "#9B59B6",
          "⚪": "#BDC3C7", "⬜": "#BDC3C7", "🤍": "#BDC3C7", "🟤": "#8D6E63", "🟫": "#8D6E63", "🤎": "#8D6E63"}


def color_of(icon, colors=None):
    """«#RRGGBB» dell'emoji del badge (mappa fissa, o relay.colors), None se ignota o assente."""
    ic = str(icon or "").strip()
    return (colors or {}).get(ic) or COLORS.get(ic) or COLORS.get(ic.rstrip("\ufe0f")) or None


def epoch(ts):
    try:
        return int(_dt.datetime.fromisoformat(str(ts)[:19]).timestamp())
    except ValueError:
        return 0


def strip_markdown(text):
    t = str(text or "")
    t = re.sub(r"`{1,3}", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", t)
    t = re.sub(r"^\s*#+\s*", "", t)
    return t.strip()


def one_line(text):
    return " ".join(str(text or "").split())


def short_of(text, n=60):
    """≤ n caratteri senza segno di taglio: alla parola intera se possibile."""
    t = one_line(strip_markdown(text))
    if len(t) <= n:
        return t
    cut = t[:n]
    return cut[:cut.rfind(" ")].rstrip(" ,;:") if " " in cut[n // 2:] else cut


def watch_line(text):
    for l in reversed(str(text or "").splitlines()):
        m = re.match(r"^\s*\W*\s*watch\s*:\s*(.+)$", l, flags=re.I)
        if m:
            return strip_markdown(m.group(1))
    return ""


def without_watch(text):
    return "\n".join(l for l in str(text or "").splitlines() if not re.match(r"^\s*\W*\s*watch\s*:", l, flags=re.I)).strip()


def esito_line(text):
    for l in reversed(str(text or "").splitlines()):
        m = re.match(r"^\s*\W*\s*esito\s*:\s*(.+)$", l, flags=re.I)
        if m:
            return strip_markdown(m.group(1))
    return ""


def project_of(cwd, root):
    c, r = str(cwd or "").rstrip("/"), str(root or "").rstrip("/")
    if r and c.startswith(r + "/"):
        return c[len(r) + 1:]
    return c.rsplit("/", 1)[-1] if c else ""


def short_name(name, prefixes=()):
    for p in sorted((p for p in prefixes if p), key=len, reverse=True):
        if name.startswith(p):
            return name[len(p):]
    return name


def tier_of(kind, tool, text, high_words=None):
    """low | medium | high: parole pericolose nel testo → high; permessi su strumenti che scrivono e domande
    → medium; strumenti che leggono → low."""
    if kind != "permission":
        return "medium"   # una domanda o un piano: il testo puo' citare «deploy» senza essere un rm -rf
    t = str(text or "")
    for w in (high_words or HIGH_WORDS):
        if w.lower() in t.lower():
            return "high"
    if tool in LOW_TOOLS:
        return "low"
    return "medium"


def state_of(row, awaiting=()):
    if row.get("status") == "dead":
        return "gone"
    if row.get("waiting") or row.get("status") == "waiting":
        return "waiting"
    if (row.get("tmux") or row.get("name")) in awaiting:
        return "awaiting"
    if row.get("status") == "busy":
        return "busy"
    return "idle"


def order_sessions(sessions):
    return sorted(sessions, key=lambda s: (ORDER.get(s["state"], 9), s["name"]))


def _events_of(ledger, sid):
    return [r for r in ledger if r.get("session_id") == sid] if sid else []


def _outcome(events):
    stops = [r for r in events if r.get("event") == "stop"]
    if not stops:
        return None
    r = stops[-1]
    tail = str(r.get("tail") or r.get("last") or "")
    esito = str(r.get("esito") or "")
    full_src = tail if esito and esito.splitlines()[0] in tail else (esito + ("\n" if esito and tail else "") + tail)
    full = strip_markdown(without_watch(full_src))[:600]
    short = watch_line(str(r.get("watch") or "")) or watch_line(tail) or esito_line(esito) or esito_line(tail) or (full.splitlines() or [""])[-1]
    return {"short": short_of(short, SHORT_MAX), "full": full, "at": epoch(r.get("ts"))}


def _turn_started(events, before=None):
    ts = [epoch(r.get("ts")) for r in events if r.get("event") in ("prompt", "start", "queue-pop")]
    if before is not None:
        ts = [t for t in ts if t <= before]
    return ts[-1] if ts else None


def build_session(row, src):
    name = short_name(row.get("name") or row.get("tmux") or "?", src.get("prefixes") or ())
    tmux = row.get("tmux") or row.get("name") or ""
    st = state_of(row, src.get("awaiting") or ())
    events = _events_of(src.get("ledger") or [], row.get("session_id") or "")
    q = (src.get("questions") or {}).get(tmux) if st == "waiting" else None
    started = int(row["started_at"] / 1000) if row.get("started_at") else None
    if st == "waiting":
        asked = int((q or {}).get("asked_at") or 0) or started or 0
        since = asked
        turn = _turn_started(events, asked) if events else started
    elif st == "gone":
        since = int(row.get("visto_ts") or 0)
        turn = None
    else:
        since = started or (_turn_started(events) or 0)
        turn = _turn_started(events) if st in ("busy", "awaiting") else None
    question = None
    if q:
        options = [{"n": i + 1, "label": str(o)} for i, o in enumerate(q.get("options") or [])]
        kind = q.get("kind") or ("ask" if (q.get("tool") or "") == "AskUserQuestion" else "plan" if q.get("tool") == "ExitPlanMode" else "permission")
        question = {"id": f"q-{asked}-1", "kind": kind, "text": one_line(q.get("text") or ""), "options": options,
                    "tier": tier_of(kind, q.get("tool"), (q.get("text") or "") + " " + str(q.get("detail") or ""), src.get("high_words")),
                    "asked_at": asked}
    rt = (src.get("runtime") or {}).get(tmux) or {}
    return {
        "id": row.get("session_id") or tmux,
        "name": name,
        "account": row.get("account") or "",
        # 1.8: personal | work — l'app non deve piu' riconoscere l'account personale dal nome «personale»
        "account_kind": (src.get("account_kinds") or {}).get(row.get("account") or "") or None,
        "project": project_of(row.get("cwd"), src.get("root")),
        "state": st,
        "since": since,
        "turn_started": turn,
        "tool": (src.get("tools") or {}).get(tmux) if st in ("busy", "awaiting") else None,
        # 1.5: l'intento del comando in corso («Run the plugin test suite»), dove `tool` dice solo «Bash cd …»
        "tool_note": (src.get("tool_notes") or {}).get(tmux) if st in ("busy", "awaiting") else None,
        "link": row.get("link") or "",
        "attached": bool(row.get("attached")),
        "followed": tmux in (src.get("follow") or ()),
        "question": question,
        "outcome": _outcome(events) if st != "waiting" or not q else None,
        "next": (src.get("next") or {}).get(tmux) or None,
        # 1.2: quando e' stato scritto quel «prossimo» (la data della riga di recap, mezzanotte locale), cosi'
        # chi legge sa se e' di oggi o di tre giorni fa e puo' ordinarlo rispetto a outcome.at
        "next_at": (src.get("next_at") or {}).get(tmux) or None,
        # 1.11: cosa sta usando la sessione — modello ({id, label}), livello di effort e percentuale di
        # contesto consumata. Null quando non si leggono in modo affidabile: mai stimati
        "model": rt.get("model") or None,
        "effort": rt.get("effort") or None,
        "context": rt.get("context") if isinstance(rt.get("context"), int) else None,
        # 1.14 (22/09/2026): la sessione e' passata da sola a un modello piu' vecchio perche' le salvaguardie hanno
        # segnalato un messaggio (Opus 5.5): {from, to, category, at} finche' resta li', altrimenti null. Si torna con
        # l'op `model` o con /model; il segno sparisce al primo turno sul modello di prima
        "fallback": _fallback(rt.get("fallback")),
        # 1.1: icona della scheda (stabile per la vita della sessione) e il suo solo colore
        "icon": (src.get("icons") or {}).get(tmux) or None,
        "color": color_of((src.get("icons") or {}).get(tmux), src.get("colors")),
    }


def _int_or_none(v):
    try:
        return None if v is None else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def kinds_of(accounts, default_account=""):
    """1.8: quale account e' personale e quale di lavoro (l'app lo deduceva dal nome «personale»). Vince
    `accounts.<nome>.kind` se c'e'; altrimenti l'account di default e' personal e gli altri work; uno solo e' personal."""
    names = list(accounts or {})
    out = {}
    for n in names:
        k = str((accounts[n] or {}).get("kind") or "").lower()
        out[n] = k if k in ("personal", "work") else ("personal" if len(names) == 1 or n == default_account else "work")
    return out


def build_quota(quota, kinds=None):
    out = {}
    for acc, q in (quota or {}).items():
        q = q or {}
        out[acc] = {"h5": _int_or_none(q.get("cinque_ore_pct", q.get("five_hour_used_pct"))),
                    "w7": _int_or_none(q.get("settimana_pct", q.get("weekly_used_pct"))),
                    "reset_w7": _int_or_none(q.get("reset_settimanale", q.get("weekly_resets_at"))),
                    # 1.3: quando riparte la finestra di 5 ore (il polso mostrava il reset settimanale sotto la
                    # percentuale delle 5 ore, e sembrava sbagliato)
                    "reset_h5": _int_or_none(q.get("reset_cinque_ore", q.get("five_hour_resets_at"))),
                    "stale": bool(q.get("vecchia", q.get("stale"))),
                    # 1.8: personale o di lavoro, senza dover conoscere il nome dell'account
                    "kind": (kinds or {}).get(acc) or None}
    return out


def build_state(src, now):
    sessions = order_sessions([build_session(r, src) for r in (src.get("rows") or [])])
    # 1.13: last_used = l'ultima trascrizione di quella cartella (epoch s) o null; l'ordine resta per nome
    projects = sorted(({"path": p.get("path", ""), "name": p.get("name", ""), "account": p.get("account", ""),
                        "last_used": p.get("last_used") if isinstance(p.get("last_used"), int) else None}
                       for p in (src.get("projects") or [])), key=lambda p: p["name"])
    night = src.get("night") or {}
    recap = src.get("recap") or {}
    state = {
        "v": V,
        "ts": int(now),
        "host": src.get("host") or "",
        "sessions": sessions,
        "quota": build_quota(src.get("quota"), src.get("account_kinds")),
        "projects": projects,
        "night": {"queued": int(night.get("queued") or 0), "running": night.get("running") or None},
        "recap": {"date": recap.get("date") or "", "items": [{"project": i.get("project", ""), "done": i.get("done", ""), "next": i.get("next", "")} for i in (recap.get("items") or [])]},
        # 1.12: modelli ed effort che il polso puo' chiedere per una sessione; null se il relay non li conosce
        "choices": src.get("choices") or None
    }
    return fit_state(state, int(src.get("state_max_kb") or 8))


def size_of(state):
    return len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())


def cut_at_word(text, n):
    """≤ n caratteri, alla parola intera se possibile, a capo conservati (per `full`)."""
    t = str(text or "")
    if len(t) <= n:
        return t
    cut = t[:n]
    i = max(cut.rfind(" "), cut.rfind("\n"))
    return cut[:i].rstrip(" ,;:\n") if i > n // 2 else cut


def fit_state(state, max_kb=8):
    """Sotto il tetto, togliendo in ordine: le sessioni finite piu' vecchie (restano le KEEP_GONE piu' recenti);
    i progetti oltre i primi dieci; `done` e `next` del recap a RECAP_CUT caratteri, a fine parola; `full` degli
    esiti a 300 caratteri, poi uguale a `short`; le voci del recap dal fondo, ma mai l'ultima; infine le sessioni
    dal fondo. Mai la domanda. 1.7 (14/09, dall'app): le voci del recap cadevano per prime, e con 9 sessioni e
    10 progetti il polso non aveva mai il recap («Ascolta il recap» assente). Prima ancora `full` cadeva per
    primo: con dieci sessioni finite ogni esito era la sola riga corta. Una sessione finita che esce dallo stato
    non genera eventi (events_between ignora le gone sparite e le gone ricomparse)."""
    cap = max_kb * 1024

    def fits():
        return size_of(state) <= cap
    if fits():
        return state
    gone = sorted((s for s in state["sessions"] if s["state"] == "gone"), key=lambda s: s.get("since") or 0)
    for s in gone[:max(0, len(gone) - KEEP_GONE)]:
        state["sessions"].remove(s)
        if fits():
            return state
    state["projects"] = state["projects"][:10]
    if fits():
        return state
    items = state["recap"]["items"]
    for it in items:
        it["done"], it["next"] = cut_at_word(it.get("done"), RECAP_CUT), cut_at_word(it.get("next"), RECAP_CUT)
    if fits():
        return state
    for n in (300, 0):
        for s in state["sessions"]:
            o = s.get("outcome")
            if o:
                o["full"] = cut_at_word(o["full"], n) if n else o["short"]
        if fits():
            return state
    while not fits() and len(items) > 1:
        items.pop()
    while not fits() and state["sessions"]:
        state["sessions"].pop()
    return state


# ------------------------------------------------------------------ eventi
def _by_name(state):
    return {s["name"]: s for s in (state or {}).get("sessions", [])}


def _hm(ts):
    return _dt.datetime.fromtimestamp(int(ts)).strftime("%H:%M") if ts else "-"


def events_between(prev, cur, now, seq=1, warn_pct=95):
    """Gli eventi dal diff di due stati (forma di events-sample.json): question (domanda nuova), answered
    (domanda sparita), outcome (esito nuovo o cambiato), gone (sessione sparita o passata a gone), launched
    (sessione nuova non gone), quota (soglia warn_pct attraversata). Torna (eventi, prossimo seq)."""
    out = []
    pv, cv = _by_name(prev), _by_name(cur)
    ts = int(now)

    def add(kind, s, title, body, ref=None):
        nonlocal seq
        out.append({"key": f"{ts}_{seq:03d}", "kind": kind, "session": s["name"] if s else None,
                    "account": s["account"] if s else None, "ts": ts, "title": title, "body": body, "ref": ref})
        seq += 1
    for name, s in cv.items():
        p = pv.get(name)
        if p is None and s["state"] != "gone":
            add("launched", s, f"▶ {name}", s.get("project") or "")
        pq, q = (p or {}).get("question"), s.get("question")
        if q and (not pq or pq.get("id") != q["id"]):
            add("question", s, f"❓ {name}", q["text"], q["id"])
        if pq and not q:
            add("answered", s, f"✓ {name}", "", pq.get("id"))
        po, o = (p or {}).get("outcome"), s.get("outcome")
        if o and p is not None and (not po or po.get("at") != o.get("at")):
            add("outcome", s, f"✓ {name}", o["short"])
        if s["state"] == "gone" and p is not None and p["state"] != "gone":
            add("gone", s, f"✗ {name}", "")
    for name, p in pv.items():
        if name not in cv and p["state"] != "gone":
            add("gone", p, f"✗ {name}", "")
    for acc, q in (cur or {}).get("quota", {}).items():
        pq = ((prev or {}).get("quota") or {}).get(acc) or {}
        for k, label in (("h5", "5 h"), ("w7", "settimana")):
            v, pvv = q.get(k), pq.get(k)
            if v is not None and v >= warn_pct and (pvv is None or pvv < warn_pct):
                add("quota", None, f"⚠ {v} % {acc}", f"reset {_hm(q.get('reset_w7'))}")
                out[-1]["account"] = acc
    return out, seq


def _fallback(fb):
    if not isinstance(fb, dict) or not fb.get("to"):
        return None
    at = fb.get("at")
    return {"from": fb.get("from") or None, "to": str(fb["to"]), "category": fb.get("category") or None,
            "at": int(at) if isinstance(at, (int, float)) and at else None}

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
  next: {tmux: «→ prossimo» dal recap del progetto} · tools: {tmux: tool in corso}
"""
import datetime as _dt
import json
import re

V = 1
ORDER = {"waiting": 0, "busy": 1, "awaiting": 1, "idle": 2, "gone": 3}
HIGH_WORDS = ["rm -rf", "git push", "deploy", "DROP", "ssh", "sudo", "--force", "git reset --hard"]
LOW_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch", "LS", "TodoWrite"}
STATE_ICON = {"waiting": "❓", "busy": "▶", "awaiting": "▶", "idle": "✓", "gone": "✗"}


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
    return {"short": short_of(short, 60), "full": full, "at": epoch(r.get("ts"))}


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
    return {
        "id": row.get("session_id") or tmux,
        "name": name,
        "account": row.get("account") or "",
        "project": project_of(row.get("cwd"), src.get("root")),
        "state": st,
        "since": since,
        "turn_started": turn,
        "tool": (src.get("tools") or {}).get(tmux) if st in ("busy", "awaiting") else None,
        "link": row.get("link") or "",
        "attached": bool(row.get("attached")),
        "followed": tmux in (src.get("follow") or ()),
        "question": question,
        "outcome": _outcome(events) if st != "waiting" or not q else None,
        "next": (src.get("next") or {}).get(tmux) or None,
    }


def _int_or_none(v):
    try:
        return None if v is None else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def build_quota(quota):
    out = {}
    for acc, q in (quota or {}).items():
        q = q or {}
        out[acc] = {"h5": _int_or_none(q.get("cinque_ore_pct", q.get("five_hour_used_pct"))),
                    "w7": _int_or_none(q.get("settimana_pct", q.get("weekly_used_pct"))),
                    "reset_w7": _int_or_none(q.get("reset_settimanale", q.get("weekly_resets_at"))),
                    "stale": bool(q.get("vecchia", q.get("stale")))}
    return out


def build_state(src, now):
    sessions = order_sessions([build_session(r, src) for r in (src.get("rows") or [])])
    projects = sorted(({"path": p.get("path", ""), "name": p.get("name", ""), "account": p.get("account", "")} for p in (src.get("projects") or [])), key=lambda p: p["name"])
    night = src.get("night") or {}
    recap = src.get("recap") or {}
    state = {
        "v": V,
        "ts": int(now),
        "host": src.get("host") or "",
        "sessions": sessions,
        "quota": build_quota(src.get("quota")),
        "projects": projects,
        "night": {"queued": int(night.get("queued") or 0), "running": night.get("running") or None},
        "recap": {"date": recap.get("date") or "", "items": [{"project": i.get("project", ""), "done": i.get("done", ""), "next": i.get("next", "")} for i in (recap.get("items") or [])]},
    }
    return fit_state(state, int(src.get("state_max_kb") or 8))


def size_of(state):
    return len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())


def fit_state(state, max_kb=8):
    """Sotto il tetto, togliendo in ordine: le voci del recap, il `full` degli esiti, i progetti oltre i primi
    dieci; poi si tronca ancora `full` (mai la domanda)."""
    cap = max_kb * 1024
    if size_of(state) <= cap:
        return state
    state["recap"]["items"] = []
    if size_of(state) <= cap:
        return state
    for s in state["sessions"]:
        if s.get("outcome"):
            s["outcome"]["full"] = s["outcome"]["short"]
    if size_of(state) <= cap:
        return state
    state["projects"] = state["projects"][:10]
    while size_of(state) > cap and state["sessions"]:
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

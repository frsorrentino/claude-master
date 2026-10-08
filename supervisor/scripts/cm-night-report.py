#!/usr/bin/env python3
"""supervisor night report — i dati della pagina «Notte» (v1, 07/10/2026).

  cm-night-report.py [--now EPOCH|ISO] [--out-dir DIR] [--json-only] [--print]

Un JSON fisso costruito dal codice, senza modello, con quello che e' successo nella finestra notturna:
- in alto quello che serve alla persona: le domande ferme (stato del relay), gli ok del registro (awaiting_ok) e le voci
  «!» della riga «Prossimi:»;
- la cronologia: un elemento per lavoro della coda `night` (night-done.jsonl, night-queue.jsonl, l'esito .md) o per
  sessione (cm-timeline, come `task board --timeline --since`), con inizio, fine ed esito;
- una scheda per progetto: le parti da TASKS.md, dal piano .md con le caselle o dai compiti del registro (in
  quest'ordine; nessuna = solo gli eventi), con i commit della finestra.

La finestra va dall'ultimo prompt della persona della sera (terminale o relay; non i `claude -p`) a `now`, non prima
delle 20:00 e non oltre 14 ore; i prompt dopo le 06:00 sono mattina e non la spostano. Senza --out-dir scrive
<data>.json e <data>.html in ~/.team-supervisor/night-report/. L'HTML e' un'anteprima statica dello stesso JSON.
Specifica: docs/plans/2026-10-07-pagina-notte-dati.md (fuori dal repo pubblico).
"""
import datetime as dt
import html
import importlib.util
import json
import os
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
CFG = cm.load(warn=False)
SCHEMA = "team-supervisor/night-report"
V = 1
EVENING_H, MORNING_H, MAX_H = 20, 6, 14
TEXT_MAX = 200
PARTS_MAX = 80
EVENTS_MAX = 40
NEXT_MAX_AGE_S = 86400   # next_at e' la mezzanotte della riga di recap: vale quello di ieri o di oggi
BOX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX~/!])\]\s+(.+?)\s*$")
BOX_STATE = {" ": "todo", "x": "done", "X": "done", "~": "running", "/": "running", "!": "blocked"}
TASK_STATE = {"done": "done", "running": "running", "failed": "blocked", "awaiting_ok": "blocked",
              "proposed": "todo", "assigned": "todo", "approved": "todo"}
SDK_RE = re.compile(rb'"entrypoint"\s*:\s*"sdk-cli"')
ESITO_RE = re.compile(r"^\**(?:esito|outcome)\**\s*:\s*\**", re.I)
STOPPED_RE = re.compile(r"\b(?:ferm[oaie]|bloccat[oaie]|blocco|interrott[oaie]|fallit[oaie]|impossibile|non riesco|"
                        r"non (?:ho )?(?:potuto|fatto|scritto)|stopped|blocked|could not|couldn't)\b", re.I)
REFLOG_NEW = ("commit", "rebase", "cherry-pick", "revert")   # voci del reflog che creano un commit (non merge, reset, checkout)

_TL = None


def tl_mod():
    global _TL
    if _TL is None:
        _TL = _load("cm-timeline")
    return _TL


def one_line(t, n=TEXT_MAX):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def real(p):
    return os.path.realpath(p) if p else ""


# ------------------------------------------------------------------ finestra
def window(now, messages):
    """{start, end, start_source, floor, last_message}. `messages` = [{at, ...}] dei prompt della persona."""
    t = dt.datetime.fromtimestamp(now)
    eve = t.replace(hour=EVENING_H, minute=0, second=0, microsecond=0)
    if t.hour < EVENING_H:
        eve -= dt.timedelta(days=1)
    morning = (eve + dt.timedelta(days=1)).replace(hour=MORNING_H)
    ev, cap = int(eve.timestamp()), int(now - MAX_H * 3600)
    floor, source = (ev, "floor_20") if ev >= cap else (cap, "cap_14h")
    upto = min(int(now), int(morning.timestamp()))
    cands = [m for m in messages if floor <= m["at"] <= upto]
    last = max(cands, key=lambda m: m["at"]) if cands else None
    return {"start": last["at"] if last else floor, "end": int(now), "start_source": "last_message" if last else source,
            "floor": floor, "last_message": last}


def is_sdk(path):
    """Una trascrizione di `claude -p` (coda notturna, lavori lanciati dalla master): i suoi prompt non sono della persona."""
    try:
        with open(path, "rb") as f:
            return bool(SDK_RE.search(f.read(65536)))
    except OSError:
        return False


def live_rows():
    try:
        p = subprocess.run([os.environ.get("CM_BIN") or str(HERE / "supervisor"), "sessions", "--json", "--no-screen"],
                           capture_output=True, text=True, timeout=60)
        return json.loads(p.stdout) if p.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def person_messages(since, now, live):
    TL = tl_mod()
    out = []
    for nm, _, _, path in TL.sessions_in(since, live=live):
        if is_sdk(path):
            continue
        for e in TL.transcript_events(path, since):
            if e["kind"] == "prompt" and e["at"] <= now:
                out.append({"at": e["at"], "session": nm, "origin": e["ref"] or "pc", "text": one_line(e["text"])})
    return out


# ------------------------------------------------------------------ fonti
def iso_epoch(s):
    try:
        return int(dt.datetime.fromisoformat(str(s)).timestamp())
    except ValueError:
        return None


def read_jsonl(p):
    out = []
    for line in Path(p).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def report_output(path):
    """Il testo sotto «## Output» dell'esito .md del turno di notte, o None."""
    try:
        with open(path, errors="replace") as f:
            text = f.read(262144)
    except OSError:
        return None
    _, sep, out = text.partition("\n## Output\n")
    return out if sep else None


def job_outcome(rc, out):
    """(esito, dettaglio) di un lavoro della coda. rc=0 dice solo che `claude -p` e' uscito: d17d7b00 (07/10) e' uscito
    con 0 fermo prima del piano, senza codice. Conta la riga «Esito:» che il lavoro scrive per ultima: se dice che e'
    fermo o bloccato, l'esito e' `stopped`. Senza «Esito:» vale la prima riga dell'output, con la stessa regola."""
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    esito = next((ln for ln in reversed(lines) if ESITO_RE.match(ln)), None)
    detail = one_line(ESITO_RE.sub("", esito) if esito else (lines[0] if lines else "")) or None
    if rc != 0:
        return "failed", detail
    if STOPPED_RE.search(esito or (lines[0] if lines else "")):
        return "stopped", detail
    return "ok", detail


def night_jobs(start, now, sources):
    N = CFG["night"]
    jobs, queue = [], []
    done_p, queue_p = Path(cm.expand(N["done_file"])), Path(cm.expand(N["queue_file"]))
    if done_p.is_file():
        for r in read_jsonl(done_p):
            end = iso_epoch(r.get("finished"))
            if end is None or not start <= end <= now:
                continue
            begin = int(r["started"]) if r.get("started") else end - int(r.get("seconds") or 0)
            rep = r.get("report") or None
            outcome, detail = job_outcome(r.get("rc"), report_output(rep) if rep else None)
            jobs.append({"kind": "night_job", "id": r.get("id"), "title": one_line(f"{os.path.basename(r.get('dir') or '')} — {r.get('prompt') or ''}"),
                         "project": real(r.get("dir")), "start": begin, "end": end, "outcome": outcome,
                         "detail": detail, "report": rep if rep and os.path.isfile(rep) else None,
                         "rc": r.get("rc")})
        sources["night_done"] = "ok"
    else:
        sources["night_done"] = "missing"
    if queue_p.is_file():
        for r in read_jsonl(queue_p):
            if r.get("started"):
                jobs.append({"kind": "night_job", "id": r.get("id"), "title": one_line(f"{os.path.basename(r.get('dir') or '')} — {r.get('prompt') or ''}"),
                             "project": real(r.get("dir")), "start": int(r["started"]), "end": None, "outcome": "running",
                             "detail": None, "report": None, "rc": None})
            else:
                queue.append({"id": r.get("id"), "project": real(r.get("dir")), "prompt": one_line(r.get("prompt")), "added": r.get("added")})
        sources["night_queue"] = "ok"
    else:
        sources["night_queue"] = "missing"
    return jobs, queue


def registry(sources):
    try:
        con = _load("cm-tasks").connect(create=False)
    except Exception as e:   # noqa: BLE001 — il registro e' facoltativo
        sources["registry"] = f"error: {one_line(e, 120)}"
        return None
    sources["registry"] = "ok" if con is not None else "missing"
    return con


def approvals(con):
    if con is None:
        return []
    out = []
    for r in con.execute("SELECT id, title, project FROM tasks WHERE state='awaiting_ok' ORDER BY updated_at"):
        a = con.execute("SELECT what, where_, at FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        out.append({"task": r["id"], "title": one_line(r["title"]), "project": real(r["project"]),
                    "what": one_line(a["what"]) if a else None, "where": one_line(a["where_"]) if a else None,
                    "requested_at": a["at"] if a else None})
    return out


def relay_state(sources):
    d = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    for f, keys in ((d / "last-state.json", ("full", "state")), (d / "local" / "state.json", (None,))):
        if not f.is_file():
            continue
        try:
            raw = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            sources["relay_state"] = f"error: {one_line(e, 120)}"
            return {}
        for k in keys:
            st = raw.get(k) if k else raw
            if isinstance(st, dict) and isinstance(st.get("sessions"), list):
                sources["relay_state"] = "ok"
                return st
    sources["relay_state"] = "missing"
    return {}


def state_project(s):
    p = s.get("project") or ""
    if p and not os.path.isabs(p):
        p = os.path.join(cm.expand(CFG["workspace"]["root"]), p)
    return real(p)


def attention(state, con):
    questions, unblock = [], []
    for s in state.get("sessions") or []:
        q = s.get("question")
        if q:
            questions.append({"session": s.get("name"), "project": state_project(s), "id": q.get("id"), "kind": q.get("kind"),
                              "text": one_line(q.get("text")), "options": q.get("options") or [], "tier": q.get("tier"),
                              "asked_at": q.get("asked_at")})
        for st in s.get("next_steps") or []:
            if st.get("blocking"):
                unblock.append({"session": s.get("name"), "project": state_project(s), "text": one_line(st.get("text"))})
    return {"questions": questions, "approvals": approvals(con), "unblock": unblock}


# ------------------------------------------------------------------ cronologia
def git_out(proj, *args):
    try:
        p = subprocess.run(["git", "-C", proj, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def made_here(proj, since):
    """Gli hash dei commit creati in questo worktree dalla finestra in poi: il reflog di HEAD e' per worktree."""
    out = git_out(proj, "reflog", "show", "--date=unix", "--format=%H%x09%gd%x09%gs", "HEAD") or ""
    hs = set()
    for line in out.splitlines():
        h, sel, subj = (line.split("\t", 2) + ["", ""])[:3]
        m = re.search(r"\{(\d+)\}", sel)
        if m and int(m.group(1)) < since:
            break
        if subj.split(" ", 1)[0].rstrip(":") in REFLOG_NEW:
            hs.add(h[:7])
    return hs


def of_job(r, jobs):
    """La trascrizione `claude -p` di un lavoro della coda: stessa cartella, dentro l'intervallo del lavoro (±2 minuti)."""
    ev = [e for e in r["events"] if e["kind"] not in ("commit", "task")] or r["events"]
    if not ev:
        return False
    proj, begin, end = real(r["project"]), ev[0]["at"], ev[-1]["at"]
    return any(j["project"] == proj and j["start"] - 120 <= begin and end <= (j["end"] or 10 ** 12) + 120 for j in jobs)


def merge_rows(rows, jobs):
    """Una riga per sessione: le conversazioni della stessa sessione nella stessa cartella (handoff, /clear, la chiusa
    e la viva) diventano una, tranne quelle dei lavori della coda, che restano del loro lavoro; una riga con gli stessi
    eventi propri di un'altra (la stessa trascrizione letta due volte) si scarta."""
    by, out, seen = {}, [], {}
    for r in rows:
        k = (r["session"], real(r["project"]))
        if of_job(r, jobs):
            out.append(dict(r, events=list(r["events"])))
            continue
        if k in by:
            m = by[k]
            m["events"] = sorted({(e["at"], e["kind"], e["text"]): e for e in m["events"] + r["events"]}.values(), key=lambda e: e["at"])
            m["live"] = m["live"] or r["live"]
            continue
        by[k] = dict(r, events=list(r["events"]))
        out.append(by[k])
    kept = []
    for r in sorted(out, key=lambda r: not r["live"]):
        own = tuple((e["at"], e["kind"], e["text"]) for e in r["events"] if e["kind"] not in ("commit", "task"))
        if own and own in seen:
            continue
        seen[own] = r
        kept.append(r)
    return kept


def assign_commits(rows, since, now):
    """I commit di un repo una volta sola, anche con piu' worktree (`git log --all` li vede da tutti): vanno al
    worktree che li ha creati (reflog), alla sua sessione piu' vicina nel tempo; se nessuno li ha creati qui (un
    fast-forward), alla sessione piu' vicina fra tutte quelle del repo."""
    TL, groups = tl_mod(), {}
    for r in rows:
        r["events"] = [e for e in r["events"] if e["kind"] != "commit"]
        p = real(r["project"])
        common = git_out(p, "rev-parse", "--path-format=absolute", "--git-common-dir") if p and os.path.isdir(p) else None
        if common:
            groups.setdefault(common.strip(), []).append(r)
    for group in groups.values():
        projs = sorted({real(r["project"]) for r in group})
        made = {p: made_here(p, since) for p in projs}
        cs = {}
        for p in projs:
            for c in TL.commits(p, since):
                if c["at"] > now:
                    continue   # niente dal futuro dell'istante chiesto, come la cronologia
                cs.setdefault(c["ref"], c)
        for h, c in cs.items():
            owner = [p for p in projs if any(x.startswith(h) or h.startswith(x) for x in made[p])]
            pool = [r for r in group if real(r["project"]) in owner] or group
            best = min(pool, key=lambda r: min((abs(x["at"] - c["at"]) for x in r["events"]), default=10 ** 9) - (10 ** 8 if r["live"] else 0))
            best["events"].append(c)
    for r in rows:
        r["events"].sort(key=lambda e: e["at"])
    return [r for r in rows if r["events"]]


def session_outcome(ev, live):
    if live and ev and ev[-1]["kind"] == "prompt":
        return "running"
    checks = [e for e in ev if e["kind"] in ("test", "task") and e["ok"] is not None]
    if checks and checks[-1]["ok"] is False:
        return "failed"
    if any(e["kind"] == "outcome" for e in ev) or (checks and checks[-1]["ok"]):
        return "ok"
    return "unknown"


def session_items(tl, jobs):
    items = []
    for r in tl["sessions"]:
        ev = r["events"]
        if not ev:
            continue
        proj, begin, end = real(r["project"]), ev[0]["at"], ev[-1]["at"]
        if of_job(r, jobs):
            continue   # la sessione `claude -p` di un lavoro della coda: e' quel lavoro, non una riga in piu'
        outs = [e["text"] for e in ev if e["kind"] == "outcome"]
        items.append({"kind": "session", "id": r["session"], "title": r["session"], "project": proj, "start": begin,
                      "end": None if session_outcome(ev, r["live"]) == "running" else end,
                      "outcome": session_outcome(ev, r["live"]), "detail": outs[-1] if outs else one_line(ev[-1]["text"]),
                      "report": None, "live": bool(r["live"]),
                      "counts": {k: sum(1 for e in ev if e["kind"] == k[:-1]) for k in ("prompts", "tests", "commits")}})
    return items


# ------------------------------------------------------------------ schede
def boxes(text):
    out = []
    for ln in text.splitlines():
        m = BOX_RE.match(ln)
        if m:
            out.append({"title": one_line(m.group(2), 160), "state": BOX_STATE[m.group(1)]})
    return out


def parts_of(proj, start, now, con):
    """(fonte, file, parti): TASKS.md, poi il piano .md con caselle toccato nella finestra, poi il registro."""
    f = Path(proj) / "TASKS.md"
    if f.is_file():
        try:
            p = boxes(f.read_text(errors="replace"))
        except OSError:
            p = []
        if p:
            return "TASKS.md", str(f), p
    plans = []
    for f in (Path(proj) / "docs" / "plans").glob("*.md"):
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if start <= mt <= now:
            plans.append((mt, f))
    for _, f in sorted(plans, reverse=True):
        try:
            p = boxes(f.read_text(errors="replace"))
        except OSError:
            continue
        if p:
            return "plan", str(f), p
    if con is not None:
        rows = [r for r in con.execute("SELECT title, state, project FROM tasks ORDER BY created_at, id") if real(r["project"]) == proj and r["state"] in TASK_STATE]
        if rows:
            return "registry", None, [{"title": one_line(r["title"], 160), "state": TASK_STATE[r["state"]]} for r in rows]
    return None, None, []


def project_cards(tl, items, att, state, start, now, con, floor):
    events, names = {}, {}
    for r in tl["sessions"]:
        p = real(r["project"])
        if p:
            events.setdefault(p, []).extend(e for e in r["events"] if e["kind"] != "prompt")
            names.setdefault(p, set()).add(r["session"])
    keys = list(events)
    for x in items + att["questions"] + att["approvals"] + att["unblock"]:
        if x.get("project") and x["project"] not in keys:
            keys.append(x["project"])
    nexts = {}
    for s in state.get("sessions") or []:
        # il «→ prossimo» viene dal recap del progetto: uno di giorni fa non e' il prossimo passo di stanotte
        if s.get("next") and (s.get("next_at") or 0) >= floor - NEXT_MAX_AGE_S:
            nexts.setdefault(state_project(s), one_line(s["next"]))
    cards = []
    for p in keys:
        if not os.path.isdir(p):
            continue
        src, f, parts = parts_of(p, start, now, con)
        waiting = [f"domanda: {q['text']}" for q in att["questions"] if q["project"] == p] \
            + [f"ok: {a['title']}" for a in att["approvals"] if a["project"] == p] \
            + [f"!: {u['text']}" for u in att["unblock"] if u["project"] == p]
        todo = next((x["title"] for x in parts if x["state"] == "todo"), None)
        ev = sorted({(e["at"], e["kind"], e["text"]): e for e in events.get(p, [])}.values(), key=lambda e: e["at"])
        if not (parts or waiting or ev):
            continue   # una sessione che nella finestra ha solo ricevuto prompt: niente da mostrare nella scheda
        cards.append({"name": os.path.basename(p.rstrip("/")) or p, "path": p, "parts_source": src, "parts_file": f,
                      "parts": parts[:PARTS_MAX], "parts_total": len(parts), "waiting_on": waiting,
                      "next": nexts.get(p) or todo, "events": ev[-EVENTS_MAX:]})
    cards.sort(key=lambda c: (-len(c["waiting_on"]), -(c["events"][-1]["at"] if c["events"] else 0), c["name"]))
    return cards


# ------------------------------------------------------------------ rapporto
def build(now=None):
    now = int(now or time.time())
    sources = {}
    live = live_rows()
    floor = window(now, [])["floor"]
    try:
        msgs = person_messages(floor, now, live)
        sources["transcripts"] = "ok"
    except Exception as e:   # noqa: BLE001 — senza prompt la finestra parte dal pavimento
        msgs, sources["transcripts"] = [], f"error: {one_line(e, 120)}"
    win = window(now, msgs)
    jobs, queue = night_jobs(win["start"], now, sources)
    try:
        tl = tl_mod().timeline(now - win["start"], limit=500, now=now, live=live)
        tl["sessions"] = assign_commits(merge_rows(tl["sessions"], jobs), win["start"], now)
        sources["timeline"] = "ok"
    except Exception as e:   # noqa: BLE001
        tl, sources["timeline"] = {"sessions": []}, f"error: {one_line(e, 120)}"
    con = registry(sources)
    state = relay_state(sources)
    att = attention(state, con)
    items = sorted(jobs + session_items(tl, jobs), key=lambda x: (x["start"], x["id"] or ""))
    cards = project_cards(tl, items, att, state, win["start"], now, con, win["floor"])
    return {"schema": SCHEMA, "v": V, "generated_at": int(time.time()), "date": dt.date.fromtimestamp(now).isoformat(),
            "window": win, "attention": att, "timeline": items, "queue": queue, "projects": cards, "sources": sources}


# ------------------------------------------------------------------ anteprima HTML
COLOR = {"ok": "#2e9e5b", "failed": "#d64545", "stopped": "#e0a020", "running": "#3b82f6", "unknown": "#9aa3ad"}
PART_MARK = {"done": "✓", "running": "▶", "blocked": "■", "todo": "○"}
CSS = """
body{font:15px/1.45 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1d232b}
main{max-width:980px;margin:0 auto;padding:20px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:26px 0 10px}
.sub{color:#5b6670;font-size:13px}
.box{background:#fff;border:1px solid #e1e5ea;border-radius:10px;padding:12px 14px;margin:8px 0}
.att{border-left:4px solid #e0a020}.att b{display:block}
.opt{display:inline-block;border:1px solid #c9d1d9;border-radius:14px;padding:1px 10px;margin:4px 6px 0 0;font-size:13px}
.lane{position:relative;height:26px;margin:4px 0;background:#eef1f4;border-radius:5px}
.bar{position:absolute;top:3px;height:20px;border-radius:4px;min-width:4px}
.lab{display:flex;justify-content:space-between;font-size:13px;margin-top:10px}
.axis{position:relative;height:16px;font-size:11px;color:#5b6670}.axis span{position:absolute;transform:translateX(-50%)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.card h3{margin:0 0 6px;font-size:15px}.parts{list-style:none;padding:0;margin:6px 0}
.parts li{font-size:13px}.done{color:#2e9e5b}.running{color:#3b82f6}.blocked{color:#d64545}.todo{color:#5b6670}
.ev{font-size:12px;color:#3c4650;margin:2px 0}.empty{color:#5b6670;font-style:italic}
@media (prefers-color-scheme:dark){body{background:#14181d;color:#e4e8ec}.box{background:#1d232b;border-color:#2c343e}
.lane{background:#262e38}.sub,.axis,.todo,.empty{color:#9aa3ad}.ev{color:#c2c9d0}}
"""


def hm(t):
    return time.strftime("%H:%M", time.localtime(t)) if t else "…"


def render_html(rep):
    e = html.escape
    w = rep["window"]
    span = max(1, w["end"] - w["start"])
    lm = w.get("last_message")
    out = [f"<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<title>Notte {e(rep['date'])}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Notte del {e(rep['date'])}</h1><div class=\"sub\">dalle {hm(w['start'])} alle {hm(w['end'])} · inizio: {e(w['start_source'])}"
           + (f" ({e(lm['session'])}, {e(lm['origin'])}: «{e(lm['text'][:80])}»)" if lm else "") + "</div>"]
    a = rep["attention"]
    out.append("<h2>Serve a te</h2>")
    if not (a["questions"] or a["approvals"] or a["unblock"]):
        out.append("<div class=\"box empty\">Niente in sospeso.</div>")
    for q in a["questions"]:
        opts = "".join(f"<span class=\"opt\">{e(str(o.get('n')))} {e(str(o.get('label')))}</span>" for o in q["options"])
        out.append(f"<div class=\"box att\"><b>Domanda · {e(q['session'] or '')}</b>{e(q['text'])}<div>{opts}</div></div>")
    for x in a["approvals"]:
        out.append(f"<div class=\"box att\"><b>Ok · {e(x['title'])}</b>{e(x['what'] or '')}" + (f" → {e(x['where'])}" if x["where"] else "") + "</div>")
    for u in a["unblock"]:
        out.append(f"<div class=\"box att\"><b>Da sbloccare · {e(u['session'] or '')}</b>{e(u['text'])}</div>")
    out.append("<h2>Cronologia</h2><div class=\"box\">")
    ticks, t0 = [], (w["start"] // 3600 + 1) * 3600
    for t in range(t0, w["end"], 3600 if span <= 8 * 3600 else 7200):
        ticks.append(f"<span style=\"left:{(t - w['start']) * 100 / span:.2f}%\">{hm(t)}</span>")
    out.append(f"<div class=\"axis\">{''.join(ticks)}</div>")
    if not rep["timeline"]:
        out.append("<div class=\"empty\">Nessun lavoro nella finestra.</div>")
    for it in rep["timeline"]:
        s = max(it["start"], w["start"])
        end = it["end"] or w["end"]
        left, width = (s - w["start"]) * 100 / span, max(0.6, (end - s) * 100 / span)
        kind = "notte" if it["kind"] == "night_job" else "sessione"
        out.append(f"<div class=\"lab\"><span><b>{e(it['title'])}</b> · {kind}</span><span>{hm(it['start'])}–{hm(it['end'])} · {e(it['outcome'])}</span></div>"
                   f"<div class=\"lane\" title=\"{e(it['detail'] or '')}\"><div class=\"bar\" style=\"left:{left:.2f}%;width:{min(width, 100 - left):.2f}%;"
                   f"background:{COLOR.get(it['outcome'], '#9aa3ad')}\"></div></div>"
                   + (f"<div class=\"ev\">{e(it['detail'])}</div>" if it["detail"] else ""))
    if rep["queue"]:
        out.append(f"<div class=\"ev\">In coda: {len(rep['queue'])} — " + e(" · ".join(os.path.basename(q["project"]) for q in rep["queue"])) + "</div>")
    out.append("</div><h2>Progetti</h2><div class=\"cards\">")
    for c in rep["projects"]:
        parts = "".join(f"<li class=\"{p['state']}\">{PART_MARK[p['state']]} {e(p['title'])}</li>" for p in c["parts"])
        more = f"<li class=\"todo\">… altre {c['parts_total'] - len(c['parts'])}</li>" if c["parts_total"] > len(c["parts"]) else ""
        src = {"TASKS.md": "da TASKS.md", "plan": "dal piano", "registry": "dal registro"}.get(c["parts_source"], "solo eventi")
        evs = "".join(f"<div class=\"ev\">{hm(x['at'])} {e(x['kind'])} {'✓' if x['ok'] else '✗' if x['ok'] is False else ''} {e(x['text'])}</div>" for x in c["events"][-8:])
        out.append(f"<div class=\"box card\"><h3>{e(c['name'])}</h3><div class=\"sub\">{src}</div>"
                   + "".join(f"<div class=\"ev blocked\">aspetta {e(x)}</div>" for x in c["waiting_on"])
                   + (f"<div class=\"ev\">→ {e(c['next'])}</div>" if c["next"] else "")
                   + (f"<ul class=\"parts\">{parts}{more}</ul>" if parts else "") + (evs or "<div class=\"empty\">nessun evento</div>") + "</div>")
    out.append("</div><p class=\"sub\">Fonti: " + e(", ".join(f"{k} {v}" for k, v in rep["sources"].items()))
               + f" · generato alle {hm(rep['generated_at'])}</p></main></body></html>")
    return "\n".join(out)


# ------------------------------------------------------------------ CLI
def parse_now(s):
    if re.fullmatch(r"\d+", s):
        return int(s)
    try:
        return int(dt.datetime.fromisoformat(s).timestamp())
    except ValueError:
        sys.exit("night report: --now wants an epoch or an ISO time")


def write(p, text):
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def main(argv):
    args = list(argv)
    if args and args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    now, out_dir = None, Path(cm.expand("~/.team-supervisor/night-report"))
    if "--now" in args:
        i = args.index("--now"); now = parse_now(args[i + 1]); del args[i:i + 2]
    if "--out-dir" in args:
        i = args.index("--out-dir"); out_dir = Path(cm.expand(args[i + 1])); del args[i:i + 2]
    rep = build(now)
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if "--print" in args:
        print(text)
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    write(out_dir / f"{rep['date']}.json", text + "\n")
    print(out_dir / f"{rep['date']}.json")
    if "--json-only" not in args:
        write(out_dir / f"{rep['date']}.html", render_html(rep))
        print(out_dir / f"{rep['date']}.html")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

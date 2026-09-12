#!/usr/bin/env python3
"""claude-master relay — il PC sul bus dell'app Wear OS (fase 1 del design docs/plans/2026-09-12-app-polso-design.md).

  claude-master relay push [--dry-run|--async]   costruisce /state dalle sorgenti esistenti (sessioni, ledger, domande,
                                                  quota, recap, notte, inventario), lo cifra e lo scrive su Firebase RTDB;
                                                  scrive /events (dal diff con la push precedente) e sveglia l'orologio
                                                  con FCM; --dry-run stampa il JSON in chiaro e non tocca la rete;
                                                  --async torna subito e pusha entro relay.debounce_s (piu' richieste
                                                  ravvicinate = una push)
  claude-master relay pair [--timeout S]          codice a 6 cifre sullo schermo, X25519 su /pair/<code>, chiave in
                                                  <relay.dir>/key, uid dell'orologio in /allowed e devices.json
  claude-master relay serve                       il daemon: stream SSE su /cmd, esegue (allow-list), /result, ripubblica
  claude-master relay ensure|status|install|uninstall|off

Ogni documento sul bus e' {"v":1,"enc":…} (cm-relay-crypto); la forma di /state e' il contratto v1 dell'app
(tests/fixtures/relay/state-*.json, costruito da cm-relay-state). Niente SDK Firebase: REST + SSE con urllib, token
OAuth2 dal service account (<relay.dir>/service-account.json, 0600, mai nel repo).

Prove: CM_RELAY_CM (dispatcher da usare per sessions/registry/quota/answer/talk/launch/screen), relay.firebase_url,
relay.token_url e relay.fcm_url sul Firebase finto (tests/lib/cm_test.fake_rtdb).
"""
import fcntl
import importlib.util
import json
import os
import re
import socket
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
S = _load("cm-relay-state")
C = _load("cm-relay-crypto")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
R = CFG["relay"]
CM_BIN = os.environ.get("CM_RELAY_CM") or str(HERE / "claude-master")
BACKOFF = [1, 2, 5, 15, 30]
OPS = ("answer", "prompt", "launch", "follow", "unfollow", "resume", "screen", "allow_all")


class RelayError(Exception):
    pass


# ------------------------------------------------------------------ file e log
def rdir():
    p = Path(cm.expand(R.get("dir") or "~/.claude-master/relay"))
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def log(line):
    p = Path(cm.expand(R.get("log") or "")) if R.get("log") else rdir() / "relay.log"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
    except OSError:
        pass


def read_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def write_json(p, obj, mode=0o600):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def run_cm(*args, timeout=None):
    p = subprocess.run([CM_BIN, *args], capture_output=True, text=True, timeout=int(timeout or R.get("command_timeout_s") or 120))
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return p.returncode, out


# ------------------------------------------------------------------ Firebase: REST, SSE, FCM
def sa_path():
    return Path(cm.expand(R.get("service_account") or "")) if R.get("service_account") else rdir() / "service-account.json"


def sa_project():
    return str(read_json(sa_path(), {}).get("project_id") or "")


def token():
    p = sa_path()
    if not p.is_file():
        raise RelayError(M("relay.no_sa", path=str(p)))
    return C.sa_token(p, R.get("token_url") or "https://oauth2.googleapis.com/token", rdir() / "token.json")


def base_url():
    u = str(R.get("firebase_url") or "").rstrip("/")
    if not u:
        raise RelayError(M("relay.no_url"))
    return u


def rtdb(method, path, obj=None, query=None, timeout=20):
    """Una chiamata REST a RTDB: GET/PUT/PATCH/DELETE su /<path>.json?access_token=… Torna il JSON (o None)."""
    q = {"access_token": token()}
    q.update(query or {})
    url = f"{base_url()}/{path.strip('/')}.json?{urllib.parse.urlencode(q)}"
    data = json.dumps(obj, ensure_ascii=False).encode() if obj is not None or method in ("PUT", "PATCH") else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def key():
    k = C.load_key(rdir())
    if not k:
        raise RelayError(M("relay.no_key"))
    return k


def put_encrypted(path, obj):
    return rtdb("PUT", path, C.encrypt(obj, key()), {"print": "silent"})


def fcm_send(data):
    """Un messaggio dati FCM sul topic (HTTP v1): sveglia l'orologio, che poi fa una GET di /state."""
    proj = sa_project()
    if not proj:
        return False
    url = f"{(R.get('fcm_url') or 'https://fcm.googleapis.com').rstrip('/')}/v1/projects/{proj}/messages:send"
    body = {"message": {"topic": str(R.get("fcm_topic") or "watch"), "data": {k: str(v) for k, v in data.items() if v is not None},
                        "android": {"priority": "high"}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
    return True


# ------------------------------------------------------------------ sorgenti di /state
def prefixes():
    return [a.get("tmux_prefix") or "" for a in CFG["accounts"].values()]


def account_for_path(path):
    """L'account dedotto dalla cartella come cm-launch.sh: prefisso piu' lungo in folder_map, altrimenti il default."""
    rp = os.path.realpath(str(path))
    best, best_len = "", 0
    for e in CFG.get("folder_map") or []:
        p = os.path.realpath(cm.expand(e.get("path") or ""))
        if p and (rp == p or rp.startswith(p + "/")) and len(p) > best_len:
            best, best_len = e.get("account") or "", len(p)
    return best or CFG.get("default_account") or ""


def inventory():
    """Le cartelle lanciabili (come la skill e il bot): <radice>/<project_dir>/* e, per «.», anche il livello sotto."""
    ws = CFG["workspace"]
    root = Path(cm.expand(ws["root"]))
    excluded = set(ws.get("excluded_dirs") or [])
    out = []
    for pd in (ws.get("project_dirs") or ["."]):
        base = root if pd in (".", "") else root / pd
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name in excluded or d.name.startswith("."):
                continue
            out.append({"path": str(d), "name": d.name, "account": account_for_path(d)})
            if pd in (".", ""):
                for dd in sorted(d.iterdir()):
                    if dd.is_dir() and dd.name not in excluded and not dd.name.startswith("."):
                        out.append({"path": str(dd), "name": dd.name, "account": account_for_path(dd)})
    return out


def _json_cmd(*args, expect="["):
    try:
        rc, out = run_cm(*args)
        return json.loads(out) if rc == 0 and out.strip().startswith(expect) else None
    except (ValueError, subprocess.TimeoutExpired):
        return None


def waiting_info(session_id):
    """Il file waiting/<sid> dell'hook: JSON {tool, input} (dal 0.4.0) o il solo nome del tool."""
    if not session_id:
        return {}
    p = Path(cm.expand(CFG["state_dir"])) / "waiting" / session_id
    try:
        raw = p.read_text().strip()
    except OSError:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {"tool": str(d)}
    except ValueError:
        return {"tool": raw}


def question_of(name, tool):
    """(testo intero, [etichette]) dallo schermo via `answer NAME --show` (cm-answer: piè di pagina esclusi)."""
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
            if ": " in q and tool == "AskUserQuestion":
                q = q.split(": ", 1)[1].strip()
    return q, opts


def bot_state():
    p = Path(cm.expand(CFG["bot"].get("state_file") or "")) if CFG["bot"].get("state_file") else Path(cm.expand(CFG["state_dir"])) / "bot-state.json"
    return read_json(p, {})


def followed():
    names = set(read_json(rdir() / "follow.json", []))
    for cs in (bot_state().get("chats") or {}).values():
        names |= set(cs.get("follow") or [])
    return names


def awaiting():
    names = set(read_json(rdir() / "awaiting.json", {}).keys())
    for cs in (bot_state().get("chats") or {}).values():
        names |= set((cs.get("awaiting") or {}).keys())
    return names


def recap_today(now):
    label = time.strftime("%d_%m_%Y", time.localtime(now))
    cache = read_json(Path(cm.expand(CFG["state_dir"])) / "recap-summaries" / f"{label}.json", {})
    best = {}
    for k, v in (cache or {}).items():
        proj = str(k).split("|")[0]
        n = int(str(k).split("|")[-1]) if str(k).split("|")[-1].isdigit() else 0
        done, nxt = (v.get("fatto", ""), v.get("prossimo", "")) if isinstance(v, dict) else (str(v or ""), "")
        if done and (proj not in best or n > best[proj][0]):
            best[proj] = (n, done, nxt)
    items = [{"project": p, "done": d, "next": nx} for p, (_, d, nx) in sorted(best.items())]
    return {"date": time.strftime("%Y-%m-%d", time.localtime(now)), "items": items}


def night_queue():
    try:
        p = Path(cm.expand(CFG["night"]["queue_file"]))
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    except (OSError, ValueError, KeyError):
        rows = []
    running = next((r.get("name") or r.get("project") or "job" for r in rows if r.get("started") or r.get("running")), None)
    return {"queued": len(rows), "running": running}


def tool_of(row):
    """Il tool in corso di una sessione che lavora: l'ultimo tool_use nella coda del transcript."""
    try:
        bot = _load("cm-bot"); ui = _load("cm-bot-ui")
        path = bot.transcript_of(row)
        if not path:
            return None
        size = os.path.getsize(path)
        events, _ = ui.transcript_events(path, max(0, size - 65536))
        tools = [e for e in events if e[0] == "tool"]
        return ui.tool_line(tools[-1][1], tools[-1][2]) if tools else None
    except Exception:   # il transcript e' un extra: mai bloccare la push
        return None


def collect_sources(now=None):
    now = now or time.time()
    bot = _load("cm-bot")
    # senza nome ne' tmux e' un processo claude fuori registro (un `claude -p`, il recap): non e' una sessione da polso
    live = [r for r in (_json_cmd("sessions", "--json") or []) if (r.get("tmux") or r.get("name"))]
    good = _json_cmd("registry", "--good", expect="{") or {}
    alive_names = {r.get("tmux") for r in live}
    rows = list(live)
    for s in good.get("sessioni", []):
        if s.get("nome") and s["nome"] not in alive_names and os.path.isdir(s.get("cartella") or ""):
            rows.append({"tmux": s["nome"], "name": s["nome"], "status": "dead", "cwd": s.get("cartella", ""), "account": s.get("account", ""),
                         "waiting": False, "session_id": s.get("session_id") or "", "link": "", "attached": False,
                         "visto_ts": S.epoch(s.get("visto", "")) if s.get("visto") else 0})
    ledger = bot.ledger_rows()
    questions, nexts, tools = {}, {}, {}
    for r in rows:
        tm = r.get("tmux") or r.get("name") or ""
        # il flag dell'hook vale anche se `sessions --json` non l'ha ancora visto (stessa regola di cm-sessions)
        if r.get("status") != "dead" and not r.get("waiting") and waiting_info(r.get("session_id") or ""):
            r["waiting"] = True
        nx = bot.next_of(r.get("cwd")) if r.get("cwd") else ""
        if nx:
            nexts[tm] = nx
        if S.state_of(r) == "waiting":
            wi = waiting_info(r.get("session_id") or "")
            tool = str(wi.get("tool") or "")
            text, opts = question_of(tm, tool)
            for _ in range(3):
                if opts or not tm:
                    break
                time.sleep(1)   # il dialogo si disegna un attimo dopo l'hook: si riprova prima di ripiegare
                text, opts = question_of(tm, tool)
            inp = wi.get("input") if isinstance(wi.get("input"), dict) else {}
            if not opts and inp.get("options"):
                # schermo non ancora disegnato (o sessione senza tmux): domanda e opzioni dal payload dell'hook
                text, opts = str(inp.get("question") or text or ""), [str(o) for o in inp["options"]]
            elif not text and inp.get("question"):
                text = str(inp["question"])
            asked = [S.epoch(x.get("ts")) for x in ledger if x.get("session_id") == r.get("session_id") and x.get("event") == "waiting"]
            detail = wi.get("input") if isinstance(wi.get("input"), (str, dict)) else ""
            questions[tm] = {"tool": tool, "text": text or (f"{tool} {json.dumps(detail, ensure_ascii=False)[:200]}" if tool else "?"),
                             "options": opts, "asked_at": asked[-1] if asked else int(now), "detail": json.dumps(detail, ensure_ascii=False) if detail else ""}
        elif r.get("status") == "busy":
            t = tool_of(r)
            if t:
                tools[tm] = t
    return {
        "host": str(R.get("host") or socket.gethostname()),
        "root": cm.expand(CFG["workspace"]["root"]), "prefixes": prefixes(), "high_words": R.get("tier_high") or None,
        "state_max_kb": R.get("state_max_kb") or 8,
        "rows": rows, "ledger": ledger, "questions": questions,
        "quota": _json_cmd("quota", "--json", expect="{") or {},
        "projects": inventory(), "night": night_queue(), "recap": recap_today(now),
        "follow": followed(), "awaiting": awaiting(), "next": nexts, "tools": tools,
    }


# ------------------------------------------------------------------ push
def push(dry_run=False, now=None):
    if dry_run:
        return _push(True, now)
    # una push alla volta: due in parallelo leggono lo stesso «stato precedente» e scrivono due volte gli stessi
    # eventi (dal vivo 16:22 del 12/09: la domanda e' arrivata due volte sul bus)
    lock = open(str(rdir() / "push.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _push(False, now)
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


def _push(dry_run=False, now=None):
    now = now or time.time()
    src = collect_sources(now)
    state = S.build_state(src, now)
    names = {S.short_name(r.get("name") or r.get("tmux") or "?", src["prefixes"]): (r.get("tmux") or r.get("name")) for r in src["rows"]}
    if dry_run:
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return state
    last_p = rdir() / "last-state.json"
    last = read_json(last_p, {})
    events, seq = S.events_between(last.get("state") or {}, state, now, int(last.get("seq") or 0) + 1,
                                   warn_pct=float((CFG.get("guard") or {}).get("warn_pct") or 95))
    k = key()
    rtdb("PUT", "state", C.encrypt(state, k), {"print": "silent"})
    if events:
        rtdb("PATCH", "events", {e["key"]: C.encrypt(e, k) for e in events}, {"print": "silent"})
    prune_events(now)
    for e in events:
        try:
            fcm_send({"kind": e["kind"], "session": e["session"], "ts": e["ts"], "key": e["key"]})
        except (urllib.error.URLError, OSError, ValueError) as ex:
            log(f"fcm FAILED: {ex}")
    write_json(last_p, {"state": state, "seq": seq - 1, "pushed_at": now, "names": names})
    log(f"push: {len(state['sessions'])} sessioni, {len(events)} eventi")
    return state


def prune_events(now):
    """Via gli eventi oltre relay.events_days (la chiave inizia con l'epoch)."""
    limit = now - float(R.get("events_days") or 7) * 86400
    try:
        keys = rtdb("GET", "events", None, {"shallow": "true"}) or {}
    except (urllib.error.URLError, OSError, ValueError):
        return
    old = {k: None for k in keys if str(k).split("_")[0].isdigit() and int(str(k).split("_")[0]) < limit}
    if old:
        try:
            rtdb("PATCH", "events", old, {"print": "silent"})
        except (urllib.error.URLError, OSError, ValueError):
            pass


def push_async():
    """Torna subito: scrive la richiesta con il suo istante e stacca un figlio che dorme relay.debounce_s e
    pusha solo se nel frattempo nessun'altra richiesta l'ha superato (piu' hook ravvicinati = una push)."""
    stamp = f"{time.time():.6f}"
    (rdir() / "push-request").write_text(stamp)
    subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "push", "--delayed", stamp], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def push_delayed(stamp):
    time.sleep(float(R.get("debounce_s") or 2))
    try:
        if (rdir() / "push-request").read_text().strip() != stamp:
            return 0   # una richiesta piu' recente pushera' lei
    except OSError:
        pass
    try:
        push()
    except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
        log(f"push (delayed) FAILED: {e}")
        return 1
    return 0


# ------------------------------------------------------------------ pair
def devices_path():
    return rdir() / "devices.json"


def pair(timeout=None):
    """Codice a 6 cifre sullo schermo; /pair/<code> = {pc_pub, host, exp}; l'orologio risponde in /pair/<code>/watch
    con {watch_pub, uid, name, check}; la chiave e' HKDF(X25519); `check` (HMAC della chiave sul codice) prova che
    l'orologio ha derivato la stessa chiave. Un uid per dispositivo: /allowed = {uid: true} (il pairing nuovo revoca
    il vecchio, la chiave e' una sola), devices.json sul PC. Esce 0 ok, 2 dopo relay.pair_attempts check sbagliati,
    3 allo scadere di relay.pair_ttl_s."""
    import hmac as _hmac
    import secrets as _secrets
    ttl = float(timeout or R.get("pair_ttl_s") or 300)
    max_attempts = int(R.get("pair_attempts") or 5)
    code = f"{_secrets.randbelow(10 ** 6):06d}"
    priv, pub = C.pair_keys()
    exp = int(time.time() + ttl)
    host = str(R.get("host") or socket.gethostname())
    rtdb("PUT", f"pair/{code}", {"pc_pub": pub, "host": host, "exp": exp}, {"print": "silent"})
    print(M("relay.pair_code", code=code)); sys.stdout.flush()
    log(f"pair: codice {code}, scade {exp}")
    attempts = 0
    try:
        while time.time() < exp:
            try:
                w = rtdb("GET", f"pair/{code}/watch")
            except (urllib.error.URLError, OSError, ValueError) as e:
                log(f"pair: lettura fallita ({e}), riprovo"); time.sleep(2); continue
            if isinstance(w, dict) and w.get("watch_pub") and w.get("uid"):
                try:
                    k = C.shared_key(priv, str(w["watch_pub"]))
                except Exception as e:   # chiave pubblica malformata
                    log(f"pair: watch_pub non valida ({e})"); k = None
                if k and _hmac.compare_digest(C.check_code(k, code), str(w.get("check") or "")):
                    uid, name = str(w["uid"]), str(w.get("name") or "watch")
                    C.save_key(rdir(), k)
                    write_json(devices_path(), {uid: {"name": name, "paired_at": int(time.time())}})
                    rtdb("PUT", "allowed", {uid: True}, {"print": "silent"})
                    # chiave nuova: gli eventi e i risultati cifrati con la vecchia non si aprono piu' → via
                    for stale in ("events", "result"):
                        try:
                            rtdb("DELETE", stale)
                        except (urllib.error.URLError, OSError, ValueError):
                            pass
                    rtdb("PUT", f"pair/{code}/ok", {"host": host, "check": C.check_code(k, code + ":pc")}, {"print": "silent"})
                    time.sleep(1)
                    rtdb("DELETE", f"pair/{code}")
                    print(M("relay.pair_ok", name=name, uid=uid, path=str(C.key_path(rdir()))))
                    log(f"pair: ok {name} ({uid})")
                    return 0
                attempts += 1
                log(f"pair: check sbagliato ({attempts}/{max_attempts})")
                rtdb("DELETE", f"pair/{code}/watch")
                if attempts >= max_attempts:
                    print(M("relay.pair_failed", n=attempts)); return 2
            time.sleep(1)
        print(M("relay.pair_timeout", s=int(ttl))); return 3
    finally:
        try:
            rtdb("DELETE", f"pair/{code}")
        except (urllib.error.URLError, OSError, ValueError, RelayError):
            pass


# ------------------------------------------------------------------ esecuzione dei comandi (allow-list)
def names_map():
    """nome nel contratto → nome tmux (dalla push piu' recente)."""
    return (read_json(rdir() / "last-state.json", {}).get("names") or {})


def tmux_of(session):
    return names_map().get(str(session or ""), str(session or ""))


def checkpoint(cwd):
    if not cwd or not os.path.isdir(os.path.join(cwd, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", cwd, "stash", "create"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def last_sessions():
    return {s["name"]: s for s in (read_json(rdir() / "last-state.json", {}).get("state") or {}).get("sessions", [])}


def execute(cmd):
    """(ok, testo) per un comando del contratto: {op, session, arg, by}. Allow-list fissa, tutto via la CLI."""
    op = str(cmd.get("op") or "")
    session = str(cmd.get("session") or "")
    arg = cmd.get("arg")
    if op not in OPS:
        return False, M("relay.cmd_not_allowed", op=op or "?")
    tm = tmux_of(session)
    info = last_sessions().get(session) or {}
    try:
        if op == "answer":
            n = int(str(arg or "0").strip() or 0)
            if not n:
                return False, M("relay.cmd_no_question", name=session)
            row = next((r for r in (_json_cmd("sessions", "--json") or []) if (r.get("tmux") or r.get("name")) == tm), None)
            if row:
                checkpoint(row.get("cwd"))
            rc, out = run_cm("answer", tm, str(n))
            if rc != 0:
                return False, out.splitlines()[0] if out else "answer failed"
            m = re.search(r"risposto\s+(\d+)\.\s+(.*?)\s{2,}", out + "  ") or re.search(r"risposto\s+(\d+)\.\s+(\S.*)$", out.splitlines()[0])
            label = m.group(2).strip() if m else ""
            return True, M("relay.cmd_answered", n=n, label=label)
        if op == "prompt":
            text = str(arg or "").strip()
            if not text:
                return False, "empty prompt"
            rc, out = run_cm("talk", tm, M("relay.prompt_prefix") + " " + text, "--no-wait")
            if rc != 0:
                return False, out.splitlines()[0] if out else "talk failed"
            aw = read_json(rdir() / "awaiting.json", {}); aw[tm] = int(time.time()); write_json(rdir() / "awaiting.json", aw)
            return True, M("relay.cmd_delivered")
        if op == "launch":
            path = str(arg or "")
            proj = next((p for p in inventory() if os.path.realpath(p["path"]) == os.path.realpath(path)), None) if path else None
            if not proj:
                return False, M("relay.cmd_no_project", path=path or "?")
            rc, out = run_cm("launch", proj["path"], "--no-window")
            if rc != 0:
                return False, out.splitlines()[0] if out else "launch failed"
            return True, M("relay.cmd_launched", name=proj["name"], account=proj["account"])
        if op in ("follow", "unfollow"):
            fl = set(read_json(rdir() / "follow.json", []))
            (fl.add if op == "follow" else fl.discard)(tm)
            write_json(rdir() / "follow.json", sorted(fl))
            return True, M("relay.cmd_following" if op == "follow" else "relay.cmd_unfollowed", name=session)
        if op == "resume":
            if info.get("state") == "gone":
                return False, M("relay.cmd_gone", name=session)
            rc, out = run_cm("talk", tm, M("relay.prompt_prefix") + " " + str((CFG.get("guard") or {}).get("resume_prompt") or "riprendi da dove eri"), "--no-wait")
            if rc != 0:
                return False, out.splitlines()[0] if out else "talk failed"
            return True, M("relay.cmd_resumed", name=session)
        if op == "screen":
            rc, out = run_cm("screen", tm, "--lines", "30")
            return (rc == 0), ("\n".join(out.splitlines()[-30:]) if rc == 0 else (out.splitlines()[0] if out else "screen failed"))
        if op == "allow_all":
            q = info.get("question") or {}
            if not q:
                return False, M("relay.cmd_no_question", name=session)
            if q.get("tier") == "high":
                return False, M("relay.cmd_high")
            opt = next((o for o in q.get("options", []) if re.search(r"don.?t ask again|non chiedere|sempre|always", str(o.get("label") or ""), re.I)), None)
            if not opt:
                return False, M("relay.cmd_no_allow_all")
            rc, out = run_cm("answer", tm, str(opt["n"]))
            return (rc == 0), (M("relay.cmd_answered", n=opt["n"], label=opt["label"]) if rc == 0 else (out.splitlines()[0] if out else "answer failed"))
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return False, M("relay.cmd_not_allowed", op=op)


# ------------------------------------------------------------------ serve (SSE su /cmd)
def serve_pid_path():
    return rdir() / "serve.pid"


def serve_status_path():
    return rdir() / "serve.json"


def serve_alive():
    try:
        pid = int(serve_pid_path().read_text().strip() or 0)
        if pid > 0:
            os.kill(pid, 0)
            return pid
    except (OSError, ValueError):
        pass
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


def sse_lines(resp):
    """Gli eventi (event, data) di uno stream SSE, riga per riga."""
    ev, data = None, []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "":
            if ev is not None:
                yield ev, "\n".join(data)
            ev, data = None, []


def commands_from(ev, payload):
    """{id: doc} dai `put`/`patch` sul nodo /cmd."""
    if ev not in ("put", "patch"):
        return {}
    try:
        d = json.loads(payload or "null") or {}
    except ValueError:
        return {}
    path, data = str(d.get("path") or "/"), d.get("data")
    if path in ("", "/"):
        return dict(data) if isinstance(data, dict) else {}
    cid = path.strip("/").split("/")[0]
    if ev == "put" and "/" not in path.strip("/"):
        return {cid: data}
    return {}


def handle_cmd(cid, doc, done):
    if doc is None:
        return False
    if cid in done:
        # gia' eseguito (Riprova dal polso, o la riconnessione che rimanda tutto /cmd): via senza rieseguire
        try:
            rtdb("DELETE", f"cmd/{cid}")
        except (urllib.error.URLError, OSError, ValueError):
            pass
        return False
    k = key()
    try:
        cmd = C.decrypt(doc, k) if isinstance(doc, dict) and "enc" in doc else doc
    except ValueError as e:
        log(f"cmd {cid}: non decifrabile ({e}), scartato"); cmd = None
    if not isinstance(cmd, dict):
        try:
            rtdb("DELETE", f"cmd/{cid}")
        except (urllib.error.URLError, OSError, ValueError):
            pass
        done.append(cid); return True
    cmd.setdefault("id", cid)
    ok, text = execute(cmd)
    log(f"cmd {cid}: {cmd.get('op')} {cmd.get('session') or ''} → {'ok' if ok else 'ERR'} {str(text)[:80]}")
    try:
        rtdb("PUT", f"result/{cid}", C.encrypt({"id": cid, "ok": bool(ok), "text": str(text), "at": int(time.time())}, k), {"print": "silent"})
        rtdb("DELETE", f"cmd/{cid}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"cmd {cid}: result non scritto ({e})")
    done.append(cid)
    del done[:-500]
    write_json(rdir() / "done-cmds.json", done)
    try:
        push()
    except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
        log(f"push dopo il comando FAILED: {e}")
    return True


def serve():
    if not R.get("enabled"):
        print(M("relay.disabled")); return 2
    lock = open(str(rdir() / "serve.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(M("relay.serve_locked")); log("serve: lock preso da un altro serve, esco"); return 4
    serve_pid_path().write_text(str(os.getpid()))
    started = time.time()
    import signal

    def _stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
    log(f"serve: avvio pid {os.getpid()}")
    done = list(read_json(rdir() / "done-cmds.json", []))
    st_ = {"pid": os.getpid(), "started": started, "last_cmd_ts": None, "last_cmd": "", "served": 0, "reconnects": 0}
    tries = 0
    try:
        while True:
            try:
                url = f"{base_url()}/cmd.json?{urllib.parse.urlencode({'access_token': token()})}"
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=float(R.get("serve_timeout_s") or 50) + 40) as resp:
                    if tries:
                        log("serve: riconnesso")
                    tries = 0
                    for ev, payload in sse_lines(resp):
                        if ev == "auth_revoked":
                            log("serve: auth_revoked, nuovo token"); (rdir() / "token.json").unlink(missing_ok=True); break
                        for cid, doc in commands_from(ev, payload).items():
                            if handle_cmd(cid, doc, done):
                                st_["last_cmd_ts"] = time.time(); st_["last_cmd"] = cid; st_["served"] += 1
                                try:
                                    serve_status_path().write_text(json.dumps(st_))
                                except OSError:
                                    pass
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    (rdir() / "token.json").unlink(missing_ok=True)
                delay = BACKOFF[min(tries, len(BACKOFF) - 1)]; tries += 1; st_["reconnects"] += 1
                log(f"serve: HTTP {e.code}, riconnessione fra {delay}s"); time.sleep(delay)
            except (urllib.error.URLError, OSError, ValueError, RelayError) as e:
                delay = BACKOFF[min(tries, len(BACKOFF) - 1)]; tries += 1; st_["reconnects"] += 1
                log(f"serve: rete assente ({str(e)[:80]}), riconnessione fra {delay}s"); time.sleep(delay)
            try:
                serve_status_path().write_text(json.dumps(st_))
            except OSError:
                pass
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
    if not R.get("enabled"):
        return 0
    pid = serve_alive()
    if pid:
        print(M("relay.serve_alive", pid=pid)); return 0
    lp = rdir() / "relay.log"
    with open(lp, "a") as out:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"], stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True, env=os.environ.copy())
    for _ in range(30):
        time.sleep(0.1)
        if serve_alive():
            break
    pid = serve_alive()
    print(M("relay.serve_started", pid=pid) if pid else M("relay.serve_stopped"))
    return 0 if pid else 1


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def cron_lines():
    shim = cm.home() / ".local" / "bin" / "claude-master"
    return [f"* * * * * {shim} relay ensure >/dev/null 2>&1", f"* * * * * {shim} relay push --async >/dev/null 2>&1"]


def install():
    if not R.get("enabled"):
        print(M("relay.disabled")); return 2
    cur = crontab_read()
    lines = [l for l in cur.splitlines() if "claude-master relay" not in l]
    lines += ["# claude-master relay: l'orologio (ascolto dei comandi, battito di /state ogni minuto)"] + cron_lines()
    crontab_write("\n".join(lines) + "\n")
    print(M("relay.cron_installed", s=int(R.get("heartbeat_s") or 60)))
    ensure()
    return 0


def uninstall():
    if serve_stop():
        print(M("relay.serve_stopped"))
    cur = crontab_read()
    if "claude-master relay" in cur:
        lines = [l for l in cur.splitlines() if "claude-master relay" not in l]
        crontab_write("\n".join(lines) + ("\n" if lines else ""))
        print(M("relay.cron_removed"))
    return 0


def off():
    """Pausa: ferma il daemon (il cron resta; `ensure` lo rialza al prossimo minuto solo se relay.enabled)."""
    print(M("relay.serve_stopped") if serve_stop() else M("relay.serve_alive", pid=0).split("(")[0].strip())
    return 0


def status():
    pid = serve_alive()
    last = read_json(rdir() / "last-state.json", {})
    st_ = read_json(serve_status_path(), {})
    serve_s = (f"VIVO pid {pid}, comandi {st_.get('served', 0)}, riconnessioni {st_.get('reconnects', 0)}" if pid else "fermo")
    print(M("relay.status", state="abilitato" if R.get("enabled") else "spento", url=R.get("firebase_url") or "-",
            key="ok" if C.load_key(rdir()) else "assente (relay pair)", sa="ok" if sa_path().is_file() else f"assente ({sa_path()})",
            serve=serve_s, last=(time.strftime("%H:%M:%S", time.localtime(last["pushed_at"])) if last.get("pushed_at") else "-"),
            cron="yes" if "claude-master relay" in crontab_read() else "no"))
    return 0


# ------------------------------------------------------------------ CLI
def main(argv):
    cmd = argv[0] if argv else ""
    rest = argv[1:]
    if cmd == "push":
        if "--dry-run" in rest:
            try:
                push(dry_run=True)
            except (subprocess.TimeoutExpired, OSError) as e:
                print(str(e), file=sys.stderr); return 1
            return 0
        if not R.get("enabled"):
            print(M("relay.disabled")); return 2
        if "--delayed" in rest:
            return push_delayed(rest[rest.index("--delayed") + 1])
        if "--async" in rest:
            push_async(); return 0
        try:
            st = push()
        except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
            print(f"relay push: {e}", file=sys.stderr); log(f"push FAILED: {e}"); return 1
        print(M("relay.pushed", n=len(st["sessions"])))
        return 0
    if cmd == "pair":
        if not R.get("enabled"):
            print(M("relay.disabled")); return 2
        tmo = float(rest[rest.index("--timeout") + 1]) if "--timeout" in rest else None
        try:
            return pair(tmo)
        except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
            print(f"relay pair: {e}", file=sys.stderr); return 1
    if cmd == "serve":
        return serve()
    if cmd == "ensure":
        return ensure()
    if cmd == "status":
        return status()
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    if cmd == "off":
        return off()
    print(M("relay.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

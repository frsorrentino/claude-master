#!/usr/bin/env python3
"""claude-master ask-ok / ok / oks — la coda degli ok dell'utente (23/09/2026).

Design: docs/plans/2026-09-23-approvazioni-casella-registro-design.md, sezione 1. Il 23/09 la master ha smistato a
mano una decina di ok (release, push, repo pubblici), alcuni finiti alla sessione sbagliata, e un ok riferito da
un'altra sessione non valeva: andava richiesto all'utente. Qui un ok ha una fonte verificabile e una prova.

  claude-master ask-ok "cosa" --kind K --target T [--commit SHA] [--risk low|medium|high] [--details TESTO|FILE] [--expires 12h]
  claude-master ok ID [--reason R]            approva   } solo da un terminale vero (tty) o se l'ultimo prompt SCRITTO
  claude-master ok --reject ID [--reason R]   rifiuta   } dall'utente in questa sessione contiene l'ID; mai da un'altra sessione
  claude-master ok --authorize --kind K --target T [--until 23:59|2026-09-24] [--note N]
                                              autorizzazione iniziale: vale per tutte le azioni K su T fino alla scadenza
  claude-master ok --revoke ID                toglie un'autorizzazione o una richiesta (sempre permesso: riduce, non concede)
  claude-master ok --check --kind K --target T [--commit SHA]   exit 0 se un ok o un'autorizzazione lo copre (cancello)
  claude-master oks [--all] [--json]          le richieste aperte e le autorizzazioni attive

kind: push, release, repo, deploy, payment, install, other. Canali in approvals.channels (tty, prompt, watch; il
polso passa dal relay, contratto 1.15). L'esito arriva alla sessione che ha chiesto dalla casella persistente
(cm-inbox): subito se e' ferma, a fine turno se lavora, alla ripartenza se e' chiusa.
"""
import datetime
import glob
import importlib.util
import json
import os
import re
import secrets
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
A = CFG.get("approvals") or {}
STATE = Path(cm.expand(CFG["state_dir"]))
DIR = STATE / "approvals"
KINDS = ("push", "release", "repo", "deploy", "payment", "install", "other")
RISKS = ("low", "medium", "high")
AUTH_WORDS = re.compile(r"(?i)\b(pubblic\w*|rilasc\w*|release|push\w*|deploy\w*|install\w*|publish\w*)\b")


def M(key, **kw):
    return cm.msg(CFG, key, **kw)


def ledger(event, **kw):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / "ledger.jsonl", "a") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event, **kw}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ archivio
def save(rec):
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{rec['id']}.json"
    tmp = p.with_suffix(".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return rec


def load(rec_id):
    try:
        return json.loads((DIR / f"{rec_id}.json").read_text())
    except (OSError, ValueError):
        return None


def records():
    out, now = [], time.time()
    for p in DIR.glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if r.get("status") in ("pending", "active") and float(r.get("expires") or 0) < now:
            r["status"] = "expired"
            save(r)
            ledger("ok-expired", id=r["id"], what=r.get("what"))
            if r.get("type") == "request":
                notify(r, M("ok.notify_expired", what=r.get("what"), id=r["id"]))
        out.append(r)
    return sorted(out, key=lambda r: r.get("created") or 0)


def new_id():
    while True:
        i = secrets.token_hex(2)
        if not (DIR / f"{i}.json").exists():
            return i


def parse_expires(v, default_s):
    if not v:
        return time.time() + default_s
    m = re.fullmatch(r"(\d+)\s*([hmd])", v)
    if m:
        return time.time() + int(m.group(1)) * {"h": 3600, "m": 60, "d": 86400}[m.group(2)]
    for fmt in ("%H:%M", "%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            t = datetime.datetime.strptime(v, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            now = datetime.datetime.now()
            t = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if t < now:
                t += datetime.timedelta(days=1)
        elif fmt == "%Y-%m-%d":
            t = t.replace(hour=23, minute=59)
        return t.timestamp()
    raise ValueError(v)


def end_of_day():
    return datetime.datetime.now().replace(hour=23, minute=59, second=59, microsecond=0).timestamp()


# ------------------------------------------------------------------ chi e' la sessione, cosa ha scritto l'utente
def my_session():
    name = ""
    pane = os.environ.get("TMUX_PANE", "")
    if pane:
        try:
            r = subprocess.run(["tmux"] + os.environ.get("CM_TMUX_ARGS", "").split() + ["display-message", "-p", "-t", pane, "#{session_name}"],
                               capture_output=True, text=True, timeout=5)
            name = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return {"tmux": name, "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID", ""), "cwd": os.getcwd(),
            "config_dir": os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")}


def last_typed_prompt():
    """L'ultimo prompt SCRITTO dall'utente in questa sessione (stessa regola del cambio di account, 0.4.16): non una
    risposta a una domanda del modello (tool_result), non un messaggio di un'altra sessione ne' l'espansione di una
    skill (isMeta). Torna (testo, timestamp) o (None, None)."""
    me = my_session()
    if not me["session_id"]:
        return None, None
    paths = glob.glob(os.path.join(glob.escape(me["config_dir"]), "projects", "*", glob.escape(me["session_id"]) + ".jsonl"))
    last = (None, None)
    for path in paths[:1]:
        for raw in open(path, errors="replace"):
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
                continue
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list):
                if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                    continue
                c = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
            if isinstance(c, str) and c.strip() and not c.lstrip().startswith(("<cross-session-message", "<task-notification", "<system-reminder")):
                last = (c, d.get("timestamp"))
    return last


def channels():
    return A.get("channels") or ["tty", "prompt", "watch"]


def human_consent(question, needle=None, words=None):
    """(ok, prova): l'utente in persona. tty → conferma su /dev/tty; altrimenti il suo ultimo prompt scritto deve
    contenere `needle` (l'id) o, per un'autorizzazione, una parola d'azione (`words`)."""
    if "tty" in channels():
        try:
            with open("/dev/tty", "r+") as tty:
                tty.write(question + " [s/N] ")
                tty.flush()
                ans = tty.readline().strip().lower()
            return ans in ("s", "si", "sì", "y", "yes"), {"channel": "tty", "at": time.time()}
        except OSError:
            pass   # nessun terminale: i comandi lanciati da Claude non ne hanno (verificato il 23/09)
    if "prompt" in channels():
        text, ts = last_typed_prompt()
        if text and ((needle and re.search(rf"\b{re.escape(needle)}\b", text)) or (words and words.search(text))):
            me = my_session()
            return True, {"channel": "prompt", "session": me["tmux"] or me["session_id"], "prompt_at": ts, "text": text.strip()[:200]}
    return False, None


# ------------------------------------------------------------------ esito alla sessione
def notify(rec, text):
    """Alla sessione che ha chiesto, dalla casella persistente: `talk` consegna subito se puo', altrimenti resta in
    attesa e arriva a fine turno o alla ripartenza."""
    to = (rec.get("session") or {}).get("tmux") or ""
    if not to:
        return
    try:
        r = subprocess.run([sys.executable, str(HERE / "cm-talk.py"), "talk", to, text, "--no-wait", "--no-native-hint"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise OSError(r.stderr)
    except (OSError, subprocess.SubprocessError):
        try:
            _load("cm-inbox").put(to, text, "claude-master ok", (rec.get("session") or {}).get("cwd") or "")
        except Exception:   # noqa: BLE001
            pass


def covers(auth, kind, target):
    return auth.get("type") == "authorization" and auth.get("status") == "active" and auth.get("kind") in (kind, "any") \
        and (auth.get("target") in (target, "*") or target.startswith(str(auth.get("target") or "\0").rstrip("/") + "/"))


def decide(rec_id, decision, proof, reason=None):
    """Chiamata anche dal relay per il polso (canale watch, contratto 1.15)."""
    r = load(rec_id)
    if not r or r.get("type") != "request":
        return False, M("ok.unknown", id=rec_id)
    if r.get("status") != "pending":
        return False, M("ok.not_pending", id=rec_id, status=r.get("status"))
    if float(r.get("expires") or 0) < time.time():
        records()
        return False, M("ok.not_pending", id=rec_id, status="expired")
    r.update(status="approved" if decision else "rejected", decided_at=time.time(), proof=proof, reason=reason)
    save(r)
    ledger("ok-decision", id=rec_id, what=r.get("what"), decision=r["status"], channel=proof.get("channel"), proof=proof,
           commit=r.get("commit"), target=r.get("target"))
    ch = {"tty": "terminale", "prompt": "messaggio scritto", "watch": "polso"}.get(proof.get("channel"), proof.get("channel"))
    when = time.strftime("%H:%M")
    text = (M("ok.notify_approved", channel=ch, when=when, what=r["what"], id=rec_id) if decision else
            M("ok.notify_rejected", channel=ch, when=when, what=r["what"], id=rec_id, reason=reason or "-"))
    notify(r, text)
    return True, M("ok.decided", id=rec_id, status=r["status"])


# ------------------------------------------------------------------ CLI
def opt(a, name, default=None):
    if name in a:
        i = a.index(name)
        v = a[i + 1] if i + 1 < len(a) else None
        del a[i:i + 2]
        return v
    return default


def ask(a):
    kind, target, commit = opt(a, "--kind"), opt(a, "--target"), opt(a, "--commit")
    risk, details, expires = opt(a, "--risk", "medium"), opt(a, "--details"), opt(a, "--expires")
    what = " ".join(x for x in a if not x.startswith("--"))
    if not what or kind not in KINDS or not target or risk not in RISKS:
        print(M("ok.usage_ask"), file=sys.stderr)
        return 2
    if details and os.path.isfile(details):
        details = Path(details).read_text()[:2000]
    for au in records():
        if covers(au, kind, target):
            ledger("ok-covered", what=what, kind=kind, target=target, commit=commit, authorization=au["id"])
            print(M("ok.covered", what=what, id=au["id"], until=time.strftime("%d/%m %H:%M", time.localtime(au["expires"]))))
            return 0
    me = my_session()
    for r in records():   # stessa richiesta gia' aperta: si aggiorna, non si duplica
        if r.get("type") == "request" and r.get("status") == "pending" and r.get("target") == target and r.get("commit") == commit \
                and (r.get("session") or {}).get("tmux") == me["tmux"]:
            r.update(what=what, details=details, risk=risk)
            save(r)
            print(M("ok.asked", what=what, id=r["id"]))
            return 0
    try:
        exp = parse_expires(expires, float(A.get("expires_h") or 12) * 3600)
    except ValueError:
        print(M("ok.usage_ask"), file=sys.stderr)
        return 2
    r = save({"type": "request", "id": new_id(), "what": what, "kind": kind, "target": target, "commit": commit, "risk": risk,
              "details": (details or "")[:2000] or None, "session": me, "created": time.time(), "expires": exp, "status": "pending"})
    ledger("ok-request", id=r["id"], what=what, kind=kind, target=target, commit=commit, risk=risk, session=me["tmux"])
    print(M("ok.asked", what=what, id=r["id"]))
    return 0


def authorize(a):
    kind, target, until, note = opt(a, "--kind"), opt(a, "--target"), opt(a, "--until"), opt(a, "--note")
    if kind not in KINDS + ("any",) or not target:
        print(M("ok.usage_authorize"), file=sys.stderr)
        return 2
    ok, proof = human_consent(M("ok.q_authorize", kind=kind, target=target), words=AUTH_WORDS)
    if ok and proof.get("channel") == "prompt" and not names_target(proof.get("text") or "", target):
        ok = False   # la frase deve nominare il repo, o la sessione deve stare nella sua cartella
    if not ok:
        print(M("ok.no_consent"), file=sys.stderr)
        return 4
    try:
        exp = parse_expires(until, 0) if until else end_of_day()
    except ValueError:
        print(M("ok.usage_authorize"), file=sys.stderr)
        return 2
    r = save({"type": "authorization", "id": new_id(), "kind": kind, "target": target, "note": note, "created": time.time(),
              "expires": exp, "status": "active", "proof": proof, "session": my_session()})
    ledger("ok-authorization", id=r["id"], kind=kind, target=target, until=exp, proof=proof)
    print(M("ok.authorized", id=r["id"], kind=kind, target=target, until=time.strftime("%d/%m %H:%M", time.localtime(exp))))
    return 0


def names_target(text, target):
    """La frase dell'utente riguarda quel target? Ne nomina l'ultimo pezzo («claude-master»), o la sessione e' nella
    cartella di quel repo (remote git)."""
    base = str(target).rstrip("/").split("/")[-1].removesuffix(".git").lower()
    if base and base in text.lower():
        return True
    try:
        origin = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        origin = ""
    slug = re.sub(r"(\.git)?/?$", "", re.sub(r"^.*github\.com[:/]", "", origin))
    return bool(slug) and str(target).lower().rstrip("/").endswith(slug)


def check(a):
    kind, target, commit = opt(a, "--kind"), opt(a, "--target"), opt(a, "--commit")
    if not kind or not target:
        print(M("ok.usage_check"), file=sys.stderr)
        return 2
    for r in records():
        if covers(r, kind, target):
            print(M("ok.check_ok", id=r["id"], how="authorization"))
            return 0
        if r.get("type") == "request" and r.get("status") == "approved" and r.get("kind") == kind and r.get("target") == target \
                and (not r.get("commit") or (commit and (commit.startswith(r["commit"]) or r["commit"].startswith(commit)))):
            print(M("ok.check_ok", id=r["id"], how="request"))
            return 0
    print(M("ok.check_missing", kind=kind, target=target, commit=commit or "-"), file=sys.stderr)
    return 1


def listing(a):
    show_all, js = "--all" in a, "--json" in a
    rs = [r for r in records() if show_all or r.get("status") in ("pending", "active")]
    if js:
        print(json.dumps(rs, ensure_ascii=False, indent=1))
        return 0
    if not rs:
        print(M("ok.none"))
        return 0
    for r in rs:
        until = time.strftime("%d/%m %H:%M", time.localtime(float(r.get("expires") or 0)))
        if r["type"] == "authorization":
            print(f"{r['id']}  {r['status']:<9} autorizzazione  {r['kind']} → {r['target']}  fino alle {until}")
        else:
            who = (r.get("session") or {}).get("tmux") or "?"
            print(f"{r['id']}  {r['status']:<9} {r['kind']:<8} {r['what']}  [{r.get('risk')}] {r['target']} "
                  f"{r.get('commit') or ''}  da {who}, scade {until}")
    return 0


def main(argv):
    a = list(argv)
    sub = a.pop(0) if a else ""
    if sub == "ask":
        return ask(a)
    if sub == "list":
        return listing(a)
    # sub == "ok"
    if "--check" in a:
        a.remove("--check")
        return check(a)
    if "--authorize" in a:
        a.remove("--authorize")
        return authorize(a)
    if "--revoke" in a:
        rid = opt(a, "--revoke")
        r = load(rid) if rid else None
        if not r or r.get("status") not in ("pending", "active"):
            print(M("ok.unknown", id=rid or ""), file=sys.stderr)
            return 1
        r.update(status="revoked", decided_at=time.time())
        save(r)
        ledger("ok-revoked", id=rid, what=r.get("what") or f"{r.get('kind')} {r.get('target')}")
        print(M("ok.revoked", id=rid))
        return 0
    reject = "--reject" in a
    if reject:
        a.remove("--reject")
    reason = opt(a, "--reason")
    rid = a[0] if a else ""
    r = load(rid)
    if not r or r.get("type") != "request":
        print(M("ok.unknown", id=rid), file=sys.stderr)
        return 1
    ok, proof = human_consent(M("ok.q_decide", verb="Rifiuti" if reject else "Approvi", what=r["what"], id=rid,
                                target=r["target"], commit=r.get("commit") or "-"), needle=rid)
    if not ok:
        print(M("ok.no_consent"), file=sys.stderr)
        return 4
    done, msg = decide(rid, not reject, proof, reason)
    print(msg, file=sys.stdout if done else sys.stderr)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

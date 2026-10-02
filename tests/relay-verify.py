#!/usr/bin/env python3
"""Verifica cm-relay.py (fase 1 dell'app polso, design docs/plans/2026-09-12-app-polso-design.md) con un
Firebase finto (RTDB REST + SSE, token, FCM), il dispatcher finto e il claude finto.

R0   le fixture in tests/fixtures/relay sono identiche al contratto v1 dell'app (claude-master-watch/contract);
     il RTDB finto: PUT/GET/PATCH/DELETE, print=silent, shallow, stream SSE (put iniziale, poi patch/put)
R1   crypto: chiave 32 byte su file 0600, AES-256-GCM {"v":1,"enc"} con nonce diverso ogni volta, chiave
     sbagliata → ValueError; X25519 + HKDF simmetrico; check del codice; token del service account (JWT RS256
     firmato, scope firebase.database + messaging) con cache su file
R2   build_state dalle sorgenti == contratto (state-1, state-2, state-3): ordine waiting/busy/idle/gone poi
     alfabetico, short ≤ 60, full ≤ 600, domanda intera, n da 1, ≤ 8 KB (fit_state)
R3   eventi dal diff di due stati == forma di events-sample (question, answered, outcome, gone, launched, quota)
R4   relay push: --dry-run stampa il JSON in chiaro senza HTTP; push scrive /state cifrato (decifrabile = dry-run),
     /events, FCM; seconda push senza cambiamenti → niente eventi; --async con debounce 2 s (due richieste →
     una scrittura); stato oltre 8 KB ridotto
R5   relay pair: codice a 6 cifre, /pair/<code> con pc_pub, orologio finto che risponde → chiave condivisa
     salvata, uid in /allowed e devices.json, /pair cancellato; check sbagliato ×5 → esce 2; timeout → 3;
     ri-pair revoca l'uid vecchio. 1.15: lo stesso documento in /pair/<id>, il telefono finto risponde li' con
     uids e names; --text stampa il JSON del QR; il QR a mezzi blocchi si decodifica (OpenCV, se c'e')
R5b  i vettori del contratto 1.15 (pair-qr.json, pair-response.json: scalari 0..31 e 32..63) → pair_accept
     produce esattamente `ok`; qr_payload = pair-qr.json
R5c  prove isolate: CLAUDE_MASTER_CONFIG di prova → pair/push/serve solo li', la configurazione principale intatta
R6   relay serve: SSE su /cmd, i sette op del contratto eseguiti via dispatcher finto → /result, /cmd cancellato,
     /state ripubblicato; duplicati ignorati; op fuori allow-list rifiutato; launch fuori da projects rifiutato;
     RTDB giù → riconnessione; status/ensure/install/uninstall/off; install e pair rifiutati senza crontab (esce 5)
R7   cm-hook.py: waiting/<sid> in JSON con tool_input; PermissionRequest/Stop/SessionStart/SessionEnd → push
     --async (relay abilitata); disabilitata → niente; bot follow/unfollow → push
"""
import base64
import json
import re
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

WATCH = Path.home() / "Desktop" / "workspaces" / "personali" / "claude-master-watch"   # percorso vero della macchina, non un nome del set demo
FIX = T.ROOT / "tests" / "fixtures" / "relay"
# 1.21: le op che il relay esegue, lette dal sorgente (l'allow-list OPS), non ricopiate a mano
import ast as _ast, re as _re0  # noqa: E401
R_OPS = list(_ast.literal_eval(_re0.search(r"^OPS = (\(.*?\))$", (T.SCRIPTS / "cm-relay.py").read_text(), _re0.M).group(1)))

# R0: fixture identiche al contratto, quello del branch master dell'app (30/09): il worktree puo' stare su un branch di
# lavoro rimasto indietro, e i test del PC davano rosso per un contratto gia' pubblicato


def app_contract(name):
    if not (WATCH / ".git").exists():
        return None
    # origin/master e' il contratto pubblicato; il master locale del checkout dell'app puo' restare indietro (30/09:
    # 41 commit dopo un merge fatto da un altro worktree). Il master locale solo se il remoto non c'e'
    for ref in ("origin/master", "master"):
        r = subprocess.run(["git", "-C", str(WATCH), "show", f"{ref}:contract/{name}"], capture_output=True)
        if r.returncode == 0:
            return r.stdout
    return None


for f in ("state-1-question", "state-2-idle", "state-3-stale", "events-sample", "cmd-result-sample", "pair-qr", "pair-response"):
    a = FIX / f"{f}.json"; b = app_contract(f"{f}.json")
    T.check(f"R0 fixture {f} identical to the app contract on its master (origin/master; skipped if the app repo is absent)", a.is_file() and (b is None or a.read_bytes() == b), f"{WATCH}@origin/master:contract/{f}.json")

# R0b: il RTDB finto
URL, CALLS, STORE = T.fake_rtdb()


def http(method, path, body=None, headers=None):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw else None)


st, body = http("PUT", "/state.json", {"v": 1, "enc": "abc"})
T.check("R0b PUT /state.json stores the document; GET reads it back", st == 200 and http("GET", "/state.json")[1] == {"v": 1, "enc": "abc"} and STORE["state"] == {"v": 1, "enc": "abc"}, str(STORE))
http("PATCH", "/cmd.json", {"u1": {"op": "answer"}})
http("PATCH", "/cmd.json", {"u2": {"op": "screen"}})
T.check("R0b PATCH adds children; shallow lists keys; DELETE removes one", set(http("GET", "/cmd.json?shallow=true")[1]) == {"u1", "u2"} and http("DELETE", "/cmd/u1.json")[0] == 200 and list(STORE["cmd"]) == ["u2"], str(STORE))
st, _ = http("PUT", "/x.json?print=silent", 1)
T.check("R0b print=silent → 204", st == 204, str(st))
events = []


def stream():
    req = urllib.request.Request(URL + "/cmd.json", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=10) as r:
        ev = None
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev != "keep-alive":
                events.append((ev, json.loads(line[6:])))
                if len(events) >= 3:
                    return


th = threading.Thread(target=stream, daemon=True); th.start()
T.wait_until(lambda: len(events) >= 1, 3)
http("PATCH", "/cmd.json", {"u3": {"op": "follow"}})
http("PUT", "/cmd/u4.json", {"op": "resume"})
T.wait_until(lambda: len(events) >= 3, 4)
T.check("R0b SSE: initial put with the node, then a patch and a put for writes under it", len(events) >= 3 and events[0][0] == "put" and events[0][1]["path"] == "/" and events[0][1]["data"] == {"u2": {"op": "screen"}} and events[1][0] == "patch" and events[1][1]["data"] == {"u3": {"op": "follow"}} and events[2] == ("put", {"path": "/u4", "data": {"op": "resume"}}), str(events))

# R1: crypto
import importlib.util as _ilu


def load(name):
    spec = _ilu.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py"); m = _ilu.module_from_spec(spec); spec.loader.exec_module(m); return m


C = load("cm-relay-crypto")
tmp = Path(T.tmpdir())
rdir = tmp / "relay"
k = C.new_key()
kp = C.save_key(rdir, k)
T.check("R1 key: 32 bytes, saved as hex in <dir>/key with mode 0600, loaded back equal; missing → None", len(k) == 32 and kp.name == "key" and oct(kp.stat().st_mode & 0o777) == "0o600" and C.load_key(rdir) == k and C.load_key(tmp / "nope") is None, str(kp))
doc = C.encrypt({"a": 1, "s": "é"}, k)
doc2 = C.encrypt({"a": 1, "s": "é"}, k)
T.check("R1 encrypt → {v:1, enc:base64}, a fresh nonce each time; decrypt round-trips", doc["v"] == 1 and set(doc) == {"v", "enc"} and doc["enc"] != doc2["enc"] and C.decrypt(doc, k) == {"a": 1, "s": "é"}, str(doc)[:80])
try:
    C.decrypt(doc, C.new_key()); bad = False
except ValueError:
    bad = True
try:
    C.decrypt({"v": 2, "enc": doc["enc"]}, k); bad2 = False
except ValueError:
    bad2 = True
T.check("R1 wrong key or wrong form → ValueError", bad and bad2, "")
a_priv, a_pub = C.pair_keys(); b_priv, b_pub = C.pair_keys()
ka, kb = C.shared_key(a_priv, b_pub), C.shared_key(b_priv, a_pub)
T.check("R1 X25519 + HKDF: both sides derive the same 32-byte key; another pair differs", ka == kb and len(ka) == 32 and ka != C.shared_key(a_priv, a_pub), "")
T.check("R1 check_code: 16 hex, depends on the key", len(C.check_code(ka, "123456")) == 16 and C.check_code(ka, "123456") != C.check_code(C.new_key(), "123456") and C.check_code(ka, "123456") == C.check_code(kb, 123456), "")
# service account di prova (RSA generata qui) e token endpoint finto
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
SA = tmp / "service-account.json"
SA.write_text(json.dumps({"type": "service_account", "project_id": "fake-project", "private_key": pem, "client_email": "relay@fake-project.iam.gserviceaccount.com", "token_uri": URL + "/token"}))
tok = C.sa_token(SA, URL + "/token", rdir / "token.json", now=1789210000)
import base64 as _b64
jwt_claims = json.loads(_b64.urlsafe_b64decode(CALLS["token"][-1].split(".")[1] + "=="))
T.check("R1 sa_token: JWT signed and exchanged (iss = client_email, scope has database + messaging, aud = token url), token cached 0600", tok == "fake-token" and jwt_claims["iss"] == "relay@fake-project.iam.gserviceaccount.com" and "firebase.database" in jwt_claims["scope"] and "firebase.messaging" in jwt_claims["scope"] and jwt_claims["aud"] == URL + "/token" and oct((rdir / "token.json").stat().st_mode & 0o777) == "0o600", str(jwt_claims))
n_tok = len(CALLS["token"])
T.check("R1 sa_token: second call within the lifetime → from the cache, no request; past it → a new request", C.sa_token(SA, URL + "/token", rdir / "token.json", now=1789210000 + 3000) == "fake-token" and len(CALLS["token"]) == n_tok and C.sa_token(SA, URL + "/token", rdir / "token.json", now=1789210000 + 3600) == "fake-token" and len(CALLS["token"]) == n_tok + 1, str(len(CALLS["token"])))

# R2: build_state == contratto
S = load("cm-relay-state")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


ROOT_WS = "/home/demo/workspaces"
SES = load("cm-sessions")   # 1.16: le letture dello schermo e del goal che alimentano /state
F1 = json.loads((FIX / "state-1-question.json").read_text())
F2 = json.loads((FIX / "state-2-idle.json").read_text())
F3 = json.loads((FIX / "state-3-stale.json").read_text())
SRC1 = {
    "host": "crostini-demo", "root": ROOT_WS, "prefixes": ["work-"], "high_words": None,
    "rows": [
        {"name": "work-ledger-api", "tmux": "work-ledger-api", "low_priority": "offered", "goal_status": None, "account": "work", "cwd": ROOT_WS + "/work/clients/ledger-api", "status": "waiting", "waiting": True, "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "link": "https://claude.ai/code/session_01CnGG8im7UG4KtjbPDx9fst", "attached": False, "started_at": 1789210000000},
        {"name": "atlas-shop", "tmux": "atlas-shop", "low_priority": "active", "suggestion": "stale text from before the turn", "goal_status": {"text": "All checkout tests green and the release tagged", "since": 1789210700, "met": False}, "account": "personal", "cwd": ROOT_WS + "/personal/atlas-shop", "status": "busy", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": True, "started_at": 1789209000000},
        {"name": "field-notes", "tmux": "field-notes", "low_priority": "off", "suggestion": "commit the README changes and open a PR", "goal_status": None, "account": "personal", "cwd": ROOT_WS + "/personal/field-notes", "status": "idle", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "link": "https://claude.ai/code/session_03fieldnotes", "attached": False, "started_at": 1789120000000},
        {"name": "work-orbit-docs", "tmux": "work-orbit-docs", "low_priority": None, "goal_status": None, "account": "work", "cwd": ROOT_WS + "/work/own/orbit-docs", "status": "dead", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000004", "link": "", "attached": False, "visto_ts": 1789200000},
    ],
    "ledger": [
        {"event": "start", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210000)},
        {"event": "prompt", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210380)},
        {"event": "waiting", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210500), "tool": "AskUserQuestion"},
        {"event": "start", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789209000)},
        {"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789210300), "last": "x", "esito": "Esito: migrations 008-011 applied, tests green.", "tail": "Esito: migrations 008-011 applied, tests green.\nThe test seeds and the admin page are still to review.\nWatch: Migrations 008-011 applied, tests green", "watch": "Watch: Migrations 008-011 applied, tests green"},
        {"event": "prompt", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789210700)},
        {"event": "start", "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "ts": iso(1789120000)},
        {"event": "stop", "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "ts": iso(1789121000), "last": "x", "esito": "Esito: README rewritten with the three sections asked for.", "tail": "Esito: README rewritten with the three sections asked for.\nWatch: README rewritten", "watch": "Watch: README rewritten"},
    ],
    "questions": {"work-ledger-api": {"tool": "AskUserQuestion", "text": "Deploy ready, waiting for the client's ok. Deploy now?", "options": ["yes", "no"], "asked_at": 1789210500}},
    "quota": {"personal": {"cinque_ore_pct": 11, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": False},
              "work": {"cinque_ore_pct": None, "settimana_pct": 75.2, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": True}},
    "projects": [{"path": ROOT_WS + "/work/own/orbit-docs", "name": "orbit-docs", "account": "work", "last_used": 1789203600}, {"path": ROOT_WS + "/personal/atlas-shop", "name": "atlas-shop", "account": "personal", "last_used": 1789210700}, {"path": ROOT_WS + "/work/clients/ledger-api", "name": "ledger-api", "account": "work", "last_used": 1789210500}],
    # 1.17: le righe della coda come le scrive `night add` (added ISO locale, prompt intero)
    "night": {"queued": 2, "running": None, "items": [
        {"id": "a3f09c1e", "dir": ROOT_WS + "/personal/atlas-shop", "prompt": "Go through the open issues labelled flaky, reproduce each one locally with the seed from its report, fix the ones that are real and write a short note in docs/notte for the others, then run the full suite twice", "account": "personal", "model": "", "effort": "", "max_turns": 40, "added": iso(1789207200)},
        {"id": "7b21d4e8", "dir": ROOT_WS + "/work/clients/ledger-api", "prompt": "Update the changelog for 2.4 and check the migration notes", "account": "work", "model": "", "effort": "", "max_turns": 40, "added": iso(1789210620)}]},
    "ops": list(R_OPS), "slash": ["compact", "clear", "exit", "context", "cost"], "recap": {"date": "2026-09-12", "items": [{"project": "atlas-shop", "done": "Migrations 008-011 applied, tests green", "next": "Review the seeds and the admin page"}, {"project": "ledger-api", "done": "Deploy ready", "next": "Wait for the go"}]},
    "follow": {"work-ledger-api"}, "awaiting": set(),
    "next": {"work-ledger-api": "Wait for the go", "atlas-shop": "Review the seeds and the admin page", "work-orbit-docs": "Pick up the pricing page"},
    "tools": {"atlas-shop": "Bash pytest -q tests"},
    "tool_notes": {"atlas-shop": "Run the test suite"},
    "icons": {"work-ledger-api": "🟦", "atlas-shop": "🟢", "field-notes": "🟡", "work-orbit-docs": "🟪"},
    "next_at": {"work-ledger-api": 1789171200, "atlas-shop": 1789171200, "work-orbit-docs": 1789171200}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]},
    # 1.11: quello che ogni sessione viva sta usando (la gone non ha trascrizione da rileggere)
    "runtime": {"work-ledger-api": {"model": {"id": "claude-opus-5[1m]", "label": "Opus 5"}, "effort": "high", "context": 62},
                "atlas-shop": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 18},
                # 1.14: field-notes e' passata da sola a Sonnet 5 dopo un messaggio segnalato
                "field-notes": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "low", "context": 4,
                                "fallback": {"from": "claude-opus-5-5", "to": "claude-sonnet-5", "category": "cyber", "at": 1789210300}}},
}
KINDS = {"personal": "personal", "work": "work"}   # 1.8: come li calcola il relay dalla config del test
SRC1["account_kinds"] = KINDS
st1 = S.build_state(SRC1, 1789210800)


def diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            d = diff(a.get(k), b.get(k), f"{path}.{k}")
            if d:
                return d
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    return "" if a == b else f"{path}: {a!r} != {b!r}"


T.check("R2 build_state(src) == state-1-question.json (four sessions, two accounts, quota, projects, night, recap)", st1 == F1, diff(st1, F1) or "equal")
# R12 (contratto 1.16, 25/09): low_priority («off» | «offered» | «active» | null) e goal ({text, since, met} | null) per sessione
# R13 (contratto 1.17, 29/09): la coda di stanotte nello stato, sempre presente; prompt a fine parola entro 160, senza «…»
T.check("R13 (1.17) state-1: night.items in queue order with id, dir, name (as projects[].name), prompt, added (epoch), started null",
        [(i["id"], i["name"], i["added"], i["started"]) for i in st1["night"]["items"]] == [("a3f09c1e", "atlas-shop", 1789207200, None), ("7b21d4e8", "ledger-api", 1789210620, None)]
        and set(st1["night"]["items"][0]) == {"id", "dir", "name", "prompt", "added", "started"}, str(st1["night"]))
_p0 = st1["night"]["items"][0]["prompt"]
T.check("R13 (1.17) a long prompt is cut at a word end within 160 characters, no «…»; a short one stays whole",
        len(_p0) <= 160 and SRC1["night"]["items"][0]["prompt"].startswith(_p0 + " ") and "…" not in _p0 and st1["night"]["items"][1]["prompt"] == SRC1["night"]["items"][1]["prompt"], _p0)
T.check("R13 (1.17) cut_words: newlines folded, exactly 160 kept, one word longer than 160 cut at 160",
        S.cut_words("a\n b", 160) == "a b" and S.cut_words("x" * 160, 160) == "x" * 160 and S.cut_words("y" * 200, 160) == "y" * 160 and S.cut_words(("w" * 9 + " ") * 16 + "z", 160) == " ".join(["w" * 9] * 16), S.cut_words(("w" * 9 + " ") * 16 + "z", 160))
T.check("R13 (1.17) an empty queue still carries night.items = [] (its presence tells the app the relay can edit the queue)",
        S.build_state({"night": {"queued": 0}}, 1)["night"] == {"queued": 0, "running": None, "items": []}, str(S.build_state({"night": {"queued": 0}}, 1)["night"]))
# R20 (contratto 1.23, 30/09): il prompt suggerito in grigio, per il chip del telefono — solo a sessione ferma al prompt
T.check("R20 (1.23) suggestion: the idle session carries its dim suggestion; the busy one gets null even if its row had one; waiting and gone null",
        [(x["name"], x["suggestion"]) for x in st1["sessions"]] == [("ledger-api", None), ("atlas-shop", None), ("field-notes", "commit the README changes and open a PR"), ("orbit-docs", None)], str([(x["name"], x["suggestion"]) for x in st1["sessions"]]))
T.check("R12 (1.16) state-1: low_priority offered/active/off/null and goal {text, since, met} on the busy session, null elsewhere",
        [x["low_priority"] for x in st1["sessions"]] == ["offered", "active", "off", None] and st1["sessions"][1]["goal"] == {"text": "All checkout tests green and the release tagged", "since": 1789210700, "met": False} and all(x["goal"] is None for i, x in enumerate(st1["sessions"]) if i != 1), str([(x["low_priority"], x["goal"]) for x in st1["sessions"]]))
_lp = SES.low_priority_on_screen
T.check("R12 (1.16) the screen decides: «Working at lower priority» → active, «/low-priority to continue now at lower priority» → offered, the menu line «Continue now at lower priority» → offered, a plain screen → off, no screen → null",
        _lp("x", "…\n  Working at lower priority · waiting for capacity\n") == "active" and _lp("x", "Usage limit reached · /low-priority to continue now at lower priority · uses your weekly limit") == "offered"
        and _lp("x", "  ❯ 1. Stop and wait for limit to reset\n    2. Continue now at lower priority\n") == "offered" and _lp("x", "  ❯ \n") == "off" and _lp("x", "") is None, "")
_gt = tmp / "goal-transcript.jsonl"
_gt.write_text('{"type":"attachment","uuid":"a","timestamp":"2026-09-25T12:00:00.000Z","attachment":{"type":"goal_status","met":false,"sentinel":true,"condition":"la suite passa"}}\n'
               '{"type":"attachment","uuid":"b","timestamp":"2026-09-25T12:10:00.000Z","attachment":{"type":"goal_status","met":false,"sentinel":false,"iterations":3,"condition":"la suite passa"}}\n')
_row = {"account": "personale", "session_id": "goal-transcript", "cwd": str(tmp)}
_orig = SES.transcript_of
SES.transcript_of = lambda r: _gt
g_open = SES.goal_of(_row)
_gt.write_text(_gt.read_text() + '{"type":"attachment","uuid":"c","timestamp":"2026-09-25T12:20:00.000Z","attachment":{"type":"goal_status","met":true,"sentinel":false,"iterations":4,"condition":"la suite passa"}}\n')
g_met = SES.goal_of(_row)
_gt.write_text(_gt.read_text() + '{"type":"attachment","uuid":"d","timestamp":"2026-09-25T12:21:00.000Z","attachment":{"type":"goal_status","met":true,"sentinel":true,"condition":"la suite passa"}}\n')
g_cleared = SES.goal_of(_row)
SES.transcript_of = _orig
T.check("R12 (1.16) goal_of: open → {condition, since = the set sentinel's timestamp (epoch), met false}; the check that finds it met → met true; the clear sentinel → none",
        g_open == {"condition": "la suite passa", "iterations": 3, "since": 1790337600, "met": False} and g_met is not None and g_met["met"] is True and g_met["since"] == 1790337600 and g_cleared is None, str((g_open, g_met, g_cleared)))
T.check("R2 rules: order waiting/busy/idle/gone then alphabetical, short ≤ 200 (1.6), full ≤ 600, n from 1, ≤ 8 KB", [x["state"] for x in st1["sessions"]] == ["waiting", "busy", "idle", "gone"] and all(len(x["outcome"]["short"]) <= 200 and len(x["outcome"]["full"]) <= 600 for x in st1["sessions"] if x["outcome"]) and [o["n"] for o in st1["sessions"][0]["question"]["options"]] == [1, 2] and S.size_of(st1) <= 8192, str(S.size_of(st1)))
SRC2 = {"host": "crostini-demo", "root": ROOT_WS, "prefixes": ["work-"],
        "rows": [{"name": "atlas-shop", "tmux": "atlas-shop", "low_priority": "off", "goal_status": None, "account": "personal", "cwd": ROOT_WS + "/personal/atlas-shop", "status": "idle", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": False, "started_at": 1789214000000}],
        "ledger": [{"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789213900), "last": "x", "esito": "Esito: seeds and admin page reviewed, 42 tests green.", "tail": "Esito: seeds and admin page reviewed, 42 tests green.\nWatch: Seeds and admin page reviewed", "watch": "Watch: Seeds and admin page reviewed"}],
        "questions": {}, "quota": {"personal": {"cinque_ore_pct": 24, "settimana_pct": 38, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": False}, "work": {"cinque_ore_pct": 3, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": False}},
        "projects": [{"path": ROOT_WS + "/personal/atlas-shop", "name": "atlas-shop", "account": "personal", "last_used": 1789213900}], "night": {"queued": 0, "running": None}, "ops": list(R_OPS), "slash": ["compact", "clear", "exit", "context", "cost"], "recap": {"date": "2026-09-12", "items": []},
        "follow": set(), "awaiting": set(), "next": {"atlas-shop": "Test deploy on staging"}, "tools": {}, "icons": {"atlas-shop": "🟢"}, "next_at": {"atlas-shop": 1789171200}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]},
        "runtime": {"atlas-shop": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 18}}}
SRC2["account_kinds"] = KINDS
st2 = S.build_state(SRC2, 1789214400)
T.check("R2 build_state(src) == state-2-idle.json", st2 == F2, diff(st2, F2) or "equal")
SRC3 = {"host": "crostini-demo", "root": ROOT_WS, "prefixes": [], "rows": [], "ledger": [], "questions": {},
        "quota": {"personal": {"cinque_ore_pct": 0, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": True}, "work": {"cinque_ore_pct": None, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": True}},
        "projects": [], "night": {"queued": 0, "running": None}, "ops": list(R_OPS), "slash": ["compact", "clear", "exit", "context", "cost"], "recap": {"date": "2026-09-12", "items": []}, "follow": set(), "awaiting": set(), "next": {}, "tools": {}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]}}
SRC3["account_kinds"] = KINDS
st3 = S.build_state(SRC3, 1789200000)
T.check("R2 build_state(src) == state-3-stale.json", st3 == F3, diff(st3, F3) or "equal")
T.check("R2 (1.8) every session carries account_kind and every quota entry its kind (personal | work)",
        all(x["account_kind"] in ("personal", "work") for x in st1["sessions"]) and st1["quota"]["personal"]["kind"] == "personal" and st1["quota"]["work"]["kind"] == "work", str([(x["name"], x["account"], x["account_kind"]) for x in st1["sessions"]]))
T.check("R2 (1.8) kinds_of: an explicit kind wins; else the default account is personal and the others work; a single account is personal",
        S.kinds_of({"a": {}, "b": {}}, "a") == {"a": "personal", "b": "work"} and S.kinds_of({"x": {}}, "") == {"x": "personal"} and S.kinds_of({"a": {"kind": "work"}, "b": {"kind": "personal"}}, "a") == {"a": "work", "b": "personal"}, "")
big = dict(SRC1); big["projects"] = [{"path": f"{ROOT_WS}/personal/p{i:03d}", "name": f"p{i:03d}", "account": "personal"} for i in range(120)]
stb = S.build_state(big, 1789210800)
T.check("R2 over 8 KB → fit_state trims (old gone sessions, projects past 10, …), the question stays whole", S.size_of(stb) <= 8192 and stb["sessions"][0]["question"]["text"] == F1["sessions"][0]["question"]["text"] and len(stb["projects"]) <= 10, str(S.size_of(stb)))
# (job-*: work- e' il prefisso tmux dell'account work; 60 progetti: lo stato resta oltre gli 8 KB finche' le sessioni
# finite non scendono a tre anche con i nomi inglesi, piu' corti)
# 14/09 dal vivo: tre sessioni al lavoro con un esito lungo + dieci finite → prima ogni full diventava short
live = dict(SRC1)
long_tail = "Ho messo in pausa a un punto pulito; i passi per riprendere sono nel piano. " * 9
live["rows"] = [{"name": f"job-{i}", "tmux": f"job-{i}", "account": "personal", "cwd": ROOT_WS + f"/personal/job-{i}", "status": "idle", "waiting": False, "session_id": f"sid-job-{i}", "link": "https://claude.ai/code/session_" + "x" * 24, "attached": False, "started_at": 1789200000000} for i in range(3)] + \
    [{"name": f"old-{i:02d}", "tmux": f"old-{i:02d}", "account": "personal", "cwd": ROOT_WS + f"/personal/old-progetto-{i:02d}", "status": "dead", "session_id": f"0000000{i:02d}-aaaa-bbbb-cccc-dddddddddddd", "link": "https://claude.ai/code/session_" + "y" * 24, "visto_ts": 1789100000 + i, "started_at": 1789000000000} for i in range(10)]
live["ledger"] = [{"event": "stop", "session_id": f"sid-job-{i}", "ts": iso(1789210000), "tail": long_tail + "\nWatch: " + "paused at a clean point; resume steps are in the plan and the tests are green on both suites", "watch": "Watch: paused at a clean point; resume steps are in the plan and the tests are green on both suites"} for i in range(3)]
live["questions"] = {}
live["projects"] = [{"path": f"{ROOT_WS}/personal/progetto-{i:02d}", "name": f"progetto-{i:02d}", "account": "personal"} for i in range(60)]
stl = S.build_state(live, 1789210800)
works = [x for x in stl["sessions"] if x["name"].startswith("job-")]
gones = [x["name"] for x in stl["sessions"] if x["state"] == "gone"]
T.check("R2 (14/09) over 8 KB with ten gone sessions: the outcomes keep a real full (> 300, not the short), the three most recent gone stay, under the cap", S.size_of(stl) <= 8192 and len(works) == 3 and all(len(x["outcome"]["full"]) > 300 and x["outcome"]["full"] != x["outcome"]["short"] for x in works) and gones == ["old-07", "old-08", "old-09"], f"{S.size_of(stl)} {[len(x['outcome']['full']) for x in works]} {gones}")
# 1.7 (14/09 dall'app: recap.items = 0 con 9 sessioni e 10 progetti): lo stesso stato con dieci voci di recap lunghe
recap_long = {"date": "2026-09-12", "items": [{"project": f"progetto-{i:02d}", "done": "Migrazioni applicate e verificate, test verdi su tutte e due le suite. " * 2, "next": "Rivedere i seed, la pagina admin e il deploy di prova su staging. " * 2} for i in range(10)]}
str_ = S.build_state(dict(live, recap=recap_long), 1789210800)
T.check("R2 (1.7) the recap is no longer the first thing to go: with ten gone sessions and thirty projects it keeps its items (done/next cut at a word), under the cap",
        S.size_of(str_) <= 8192 and len(str_["recap"]["items"]) >= 1 and all(len(i["done"]) <= S.RECAP_CUT and len(i["next"]) <= S.RECAP_CUT for i in str_["recap"]["items"]), f"{S.size_of(str_)} items={len(str_['recap']['items'])}")
import copy
tiny = S.fit_state(copy.deepcopy(dict(S.build_state(dict(live, recap=recap_long), 1789210800))), max_kb=2)
# 23/09 (dal polso: «Session closed» per sessioni vive e ferme): fit_state toglieva sessioni VIVE dal fondo quando lo
# stato sfiorava gli 8 KB, e gli eventi calcolati sullo stato tagliato davano «gone»
crowd = S.build_state(live, 1789210800, fit=False)
for i in range(3):   # un caso in cui i tagli leggeri bastano; oltre (13 sessioni da ~600 byte) una sessione deve cadere
    crowd["sessions"].append(dict(copy.deepcopy(crowd["sessions"][0]), name=f"viva-{i}", state="idle", question=None,
                                  tool_note="nota " * 20, next="prossimo passo " * 8))
crowd["projects"] = crowd["projects"] * 3
cut = S.fit_state(copy.deepcopy(crowd), max_kb=8)
live_names = {x["name"] for x in crowd["sessions"] if x["state"] != "gone"}
T.check("R2 (23/09) a state over the cap: the lighter cuts (idle sessions' tool_note/next, projects to five) keep every live session",
        S.size_of(crowd) > 8192 and live_names <= {x["name"] for x in cut["sessions"]} and S.size_of(cut) <= 8192,
        f"{S.size_of(crowd)} → {S.size_of(cut)} {sorted(live_names - {x['name'] for x in cut['sessions']})}")
tight = S.fit_state(copy.deepcopy(crowd), max_kb=3)
ev_full, _ = S.events_between(crowd, crowd, 1789210900, 1)
dropped = live_names - {x["name"] for x in tight["sessions"]}
ev_cut, _ = S.events_between(crowd, tight, 1789210900, 1)
T.check("R2 (23/09) events come from the full state: a live session trimmed from the published state is not «gone» (the relay diffs the full states)",
        dropped and not [e for e in ev_full if e["kind"] == "gone"] and [e for e in ev_cut if e["kind"] == "gone"],
        f"dropped={sorted(dropped)} gone_full={[e['session'] for e in ev_full if e['kind'] == 'gone']}")
relay_src = (T.SCRIPTS / "cm-relay.py").read_text()
T.check("R2 (23/09) _push diffs full states and keeps the full one in last-state.json",
        "full = S.build_state(src, now, fit=False)" in relay_src and 'last.get("full") or last.get("state")' in relay_src
        and '"full": full' in relay_src, "")
T.check("R2 (1.7) even under a tiny cap one recap item stays, cut to RECAP_CUT at a word (items go from the end, never the last)",
        len(tiny["recap"]["items"]) == 1 and tiny["recap"]["items"][0]["project"] == "progetto-00" and len(tiny["recap"]["items"][0]["done"]) <= S.RECAP_CUT, str(tiny["recap"]))
T.check("R2 (1.6) short = the whole Watch line up to 200, cut at a word, no «…»", works[0]["outcome"]["short"] == "paused at a clean point; resume steps are in the plan and the tests are green on both suites" and S.short_of("parola " * 40, S.SHORT_MAX) == ("parola " * 40)[:200].rsplit(" ", 1)[0].rstrip() and "…" not in S.short_of("parola " * 40, S.SHORT_MAX), works[0]["outcome"]["short"])
T.check("R2 cut_at_word keeps newlines and cuts at a word", S.cut_at_word("uno due\ntre quattro", 12) == "uno due\ntre" and S.cut_at_word("corto", 50) == "corto", repr(S.cut_at_word("uno due\ntre quattro", 12)))
T.check("R2 tier_of: dangerous words in a PERMISSION → high; Read → low; Bash → medium; ask/plan → always medium", S.tier_of("permission", "Bash", "rm -rf build") == "high" and S.tier_of("permission", "Read", "cat x") == "low" and S.tier_of("permission", "Bash", "ls") == "medium" and S.tier_of("ask", "AskUserQuestion", "Deploy now?") == "medium" and S.tier_of("ask", None, "git push origin main?") == "medium" and S.tier_of("permission", "Bash", "git push origin main") == "high", "")
T.check("R2 (1.5) tool_note carries the intent of the running command (null when the session is not working)", st1["sessions"][1]["tool_note"] == "Run the test suite" and st1["sessions"][2]["tool_note"] is None, str([(x["name"], x["tool_note"]) for x in st1["sessions"]]))
T.check("R2 (1.3) quota carries reset_h5, when the 5-hour window restarts (before, the watch showed the weekly reset under the 5-hour figure)", st1["quota"]["personal"]["reset_h5"] == 1789228800 and st1["quota"]["work"]["reset_h5"] == 1789225200 and S.build_quota({"x": {}})["x"]["reset_h5"] is None, str(st1["quota"]))
T.check("R2 (1.2) next_at: the date of the recap line that produced «next» (null when there is no next)", st1["sessions"][0]["next_at"] == 1789171200 and st1["sessions"][2]["next"] is None and st1["sessions"][2]["next_at"] is None, str([(x["name"], x["next_at"]) for x in st1["sessions"]]))
T.check("R2 (1.1) color_of: circle/square/heart of the same hue → the same hex; unknown or empty → None; relay.colors overrides", S.color_of("🟠") == "#F5A623" and S.color_of("🟧") == "#F5A623" and S.color_of("🧡") == "#F5A623" and S.color_of("❤️") == "#E74C3C" and S.color_of("⬜") == "#BDC3C7" and S.color_of("") is None and S.color_of("🐙") is None and S.color_of("🟠", {"🟠": "#111111"}) == "#111111", "")
T.check("R2 (1.1) a session without an icon → icon and color null (old readers: grey)", S.build_session({"name": "x", "tmux": "x", "status": "idle"}, {"root": "/", "prefixes": []})["icon"] is None and S.build_session({"name": "x", "tmux": "x", "status": "idle"}, {"root": "/", "prefixes": []})["color"] is None, "")
T.check("R2 awaiting state: a session with a wrist prompt pending is «awaiting» (ordered with busy)", S.state_of({"status": "idle", "tmux": "x"}, {"x"}) == "awaiting" and S.ORDER["awaiting"] == S.ORDER["busy"], "")

# R3: eventi dal diff
EV = json.loads((FIX / "events-sample.json").read_text())
ev, seq = S.events_between(F2, F1, 1789210800, 1)
T.check("R3 state-2 → state-1: launched ledger-api, question ledger-api, outcome atlas-shop, launched field-notes; keys <ts>_<seq>; shape of events-sample", [(e["kind"], e["session"]) for e in ev] == [("launched", "ledger-api"), ("question", "ledger-api"), ("outcome", "atlas-shop"), ("launched", "field-notes")] and ev[0]["key"] == "1789210800_001" and ev[3]["key"] == "1789210800_004" and seq == 5 and all(set(e) == set(EV[0]) for e in ev) and ev[1]["title"] == "❓ ledger-api" and ev[1]["body"] == F1["sessions"][0]["question"]["text"] and ev[1]["ref"] == "q-1789210500-1" and ev[2]["body"] == "Migrations 008-011 applied, tests green" and ev[0]["body"] == "work/clients/ledger-api", str(ev))
ev2, _ = S.events_between(F1, F2, 1789214400, 1)
T.check("R3 state-1 → state-2: outcome atlas-shop (new at), gone ledger-api and field-notes (vanished), nothing for orbit-docs (already gone)", [(e["kind"], e["session"]) for e in ev2] == [("outcome", "atlas-shop"), ("gone", "ledger-api"), ("gone", "field-notes")] and ev2[1]["title"] == "✗ ledger-api", str(ev2))
q_prev = {"sessions": [F1["sessions"][0]], "quota": {"personal": {"h5": 90, "w7": 30, "reset_w7": 1789610400, "stale": False}}}
q_cur = {"sessions": [dict(F1["sessions"][0], question=None)], "quota": {"personal": {"h5": 96, "w7": 30, "reset_w7": 1789610400, "stale": False}}}
ev3, _ = S.events_between(q_prev, q_cur, 1789210900, 7)
T.check("R3 question gone → answered (ref = question id); quota crossing warn_pct → «⚠ 96 % personal» with the reset time", [(e["kind"], e["key"]) for e in ev3] == [("answered", "1789210900_007"), ("quota", "1789210900_008")] and ev3[0]["ref"] == "q-1789210500-1" and ev3[1]["title"] == "⚠ 96 % personal" and ev3[1]["account"] == "personal" and ev3[1]["body"].startswith("reset "), str(ev3))

# R4: relay push contro il Firebase finto, con dispatcher finto e stato finto della macchina
home = tmp / "home"; (home / ".claude" / "waiting").mkdir(parents=True)
ws = home / "ws"
for d in ("personal/atlas-shop/docs", "personal/field-notes", "work/clients/ledger-api", "work/own/orbit-docs", ".claude"):
    (ws / d).mkdir(parents=True)
(ws / "personal" / "atlas-shop" / "docs" / "recap.md").write_text("# Recap\n\n- 2026-09-12: Migrazioni applicate · prossimo: Review the seeds and the admin page\n")
state_dir = home / ".claude"
argslog = tmp / "cm-args.log"
alive = tmp / "alive.json"
good_json = tmp / "good.json"      # la fotografia del registro (registry --good)
launch_adds = tmp / "launch-adds.json"   # se c'e', launch la copia su alive: la sessione nata dal lancio
GOOD_ORBIT = {"nome": "work-orbit-docs", "cartella": str(ws / "work" / "own" / "orbit-docs"), "account": "work", "visto": "2026-09-12T09:00:00"}
good_json.write_text(json.dumps({"sessioni": [GOOD_ORBIT]}))
fake_cm = tmp / "claude-master"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  sessions) cat "{alive}" ;;
  registry) if [ "$2" != "--closed" ]; then cat "{good_json}"; fi ;;
  quota) echo '{{"personal": {{"cinque_ore_pct": 11, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": false}}, "work": {{"cinque_ore_pct": null, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": true}}}}' ;;
  answer) if [ "$3" = "--show" ]; then if [ "$2" = "work-ledger-api" ]; then echo "«$2» chiede — Deploy: Deploy ready, waiting for the client ok. Deploy now?"; echo "  ❯ 1. yes"; echo "    2. no"; else echo "nessuna domanda aperta sullo schermo"; exit 1; fi; else case "$3" in 1|2) echo "«$2»: risposto $3. yes  (Deploy now?)" ;; --text) echo "«$2»: risposto 3. $4  (Deploy now?)" ;; --chat) echo "«$2»: risposto 4. Chat about this  (Deploy now?)" ;; *) echo "opzione $3 inesistente" >&2; exit 2 ;; esac; fi ;;
  screen) i=1; while [ $i -le 30 ]; do echo "riga $i dello schermo"; i=$((i+1)); done ;;
  talk) if [ -f "{tmp / 'talk-saved'}" ]; then echo "claude-master talk: «$2» è chiusa; messaggio m1 salvato nella casella, le arriva quando riparte (stato: claude-master talk --status m1)" >&2; else echo "consegnato"; fi ;;
  model) echo "$2: model Sonnet 5, this session only" ;;
  effort) if [ "$2" = "atlas-shop" ]; then echo "atlas-shop is working: try again when it is idle"; exit 3; else echo "$2: effort $3, this session only"; fi ;;
  report) if [ "$3" != "-" ]; then cp "$3" "{tmp / 'report-img'}"; echo "segnalazione consegnata a «$6»"; echo "  immagine: $2/docs/segnalazioni/2026-09-12-the-client-says-the-checkout-button-is-g.jpg"; else echo "segnalazione consegnata a «$6»"; fi ;;
  interrupt) case "$2" in atlas-shop) echo "$2: fermata" ;; field-notes) echo "$2: niente da fermare" >&2; exit 1 ;; *) echo "claude-master interrupt: «$2» non è viva" >&2; exit 3 ;; esac ;;
  night) case "$2" in
      add) if [ "$4" = "FULL" ]; then echo "coda piena: 8 lavori, night.max_queued è 8" >&2; exit 4; fi; echo "in coda: 7b21d4e8 · $3 · account «work» (2 in coda)" ;;
      remove) case "$3" in 5d0e6b92) echo "tolta: $3" ;; a3f09c1e) echo "$3 è già partito: non si può togliere" >&2; exit 5 ;; *) echo "nessuna voce con id $3" >&2; exit 1 ;; esac ;;
    esac ;;
  launch) echo "sessione avviata"; echo "  link: https://claude.ai/code/session_01NEW"; if [ -f "{launch_adds}" ]; then cp "{launch_adds}" "{alive}"; fi ;;
esac
""")
fake_cm.chmod(0o755)


def rows_alive(*names, **over):
    base = {"ledger-api": {"pid": 7, "name": "work-ledger-api", "tmux": "work-ledger-api", "cwd": str(ws / "work" / "clients" / "ledger-api"), "status": "waiting", "waiting": True, "link": "https://claude.ai/code/session_01L", "account": "work", "session_id": "S-L", "attached": False, "started_at": 1789210000000},
            "atlas-shop": {"pid": 8, "name": "atlas-shop", "tmux": "atlas-shop", "cwd": str(ws / "personal" / "atlas-shop"), "status": "busy", "waiting": False, "link": "https://claude.ai/code/session_01A", "account": "personal", "session_id": "S-A", "attached": True, "started_at": 1789209000000},
            "field-notes": {"pid": 9, "name": "field-notes", "tmux": "field-notes", "cwd": str(ws / "personal" / "field-notes"), "status": "idle", "waiting": False, "link": "https://claude.ai/code/session_01F", "account": "personal", "session_id": "S-F", "attached": False, "started_at": 1789120000000}}
    rows = [dict(base[n], **over.get(n, {})) for n in names]
    alive.write_text(json.dumps(rows))


rows_alive("ledger-api", "atlas-shop", "field-notes")
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {"questions": [{"question": "Deploy now?"}]}}))
ledger = state_dir / "ledger.jsonl"
ledger.write_text("\n".join(json.dumps(r) for r in [
    {"ts": iso(1789210380), "event": "prompt", "session_id": "S-L"},
    {"ts": iso(1789210500), "event": "waiting", "session_id": "S-L", "tool": "AskUserQuestion"},
    {"ts": iso(1789210300), "event": "stop", "session_id": "S-A", "last": "x", "esito": "Esito: migrations 008-011 applied, tests green.", "tail": "Esito: migrations 008-011 applied, tests green.\nRestano da rivedere i seed.\nWatch: Migrazioni applicate, test verdi", "watch": "Watch: Migrazioni applicate, test verdi"},
    {"ts": iso(1789210700), "event": "prompt", "session_id": "S-A"},
]) + "\n")
tgdir = home / ".claude" / "channels" / "telegram"
tgdir.mkdir(parents=True, exist_ok=True)
(tgdir / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tgdir / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
TG_API, TG_CALLS, _ = T.fake_telegram()
rdir2 = tmp / "relay-live"
C.save_key(rdir2, k)
cfg = tmp / "config.json"


def write_cfg(enabled=True, **extra):
    d = {"language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state_dir), "default_account": "personal",
         "workspace": {"root": str(ws), "excluded_dirs": [".git"], "project_dirs": ["personal", "work/clients", "work/own"]},
         "folder_map": [{"path": str(ws / "work"), "account": "work"}, {"path": str(ws / "personal"), "account": "personal"}],
         "bot": {"api_base": TG_API, "token_file": str(tgdir / ".env"), "access_file": str(tgdir / "access.json")},
         "accounts": {"personal": {"config_dir": str(home / ".claude")}, "work": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "work-"}},
         "relay": {"enabled": enabled, "firebase_url": URL, "service_account": str(SA), "token_url": URL + "/token", "fcm_url": URL,
                   "dir": str(rdir2), "host": "crostini-test", "debounce_s": 1, "fcm_topic": "watch", **extra},
         # i modelli delle fixture del contratto (Opus 5, 1.12): dal 22/09 il default e' Opus 5.5 (2.1.280), e le fixture
         # restano identiche a quelle dell'app (R0), che si aggiornano dal suo lato
         "tune": {"models": [{"id": m["id"], "label": m["label"], "pick": m["label"]}
                             for m in json.loads((FIX / "state-1-question.json").read_text())["choices"]["models"]]}}
    cfg.write_text(json.dumps(d))


write_cfg()
rdir2.mkdir(parents=True, exist_ok=True)
(rdir2 / "follow.json").write_text(json.dumps(["work-ledger-api"]))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm)}


def relay(*args, timeout=60, env=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), *args], capture_output=True, text=True, env=env or ENV, timeout=timeout)


def cm_calls():
    return argslog.read_text().splitlines() if argslog.exists() else []


# 14/09 (dall'app): attesa vista solo sullo schermo, senza file dell'hook e senza nulla da estrarre → niente «?»
rows_alive("ledger-api", "atlas-shop", "field-notes", **{"field-notes": {"status": "waiting", "waiting": True}})
fq = next(x for x in json.loads(relay("push", "--dry-run").stdout)["sessions"] if x["name"] == "field-notes")
T.check("R4 waiting seen only on screen, no waiting/<sid>, nothing extracted → no question, the session back to idle (no open turn in the ledger), never «?»", fq["state"] == "idle" and fq["question"] is None, str(fq))
# senza evento waiting nel ledger l'asked_at e' il primo avvistamento (la push precedente), non «adesso»
rows_alive("ledger-api", "atlas-shop", "field-notes")
ledger_bak = ledger.read_text()
ledger.write_text("\n".join(l for l in ledger_bak.splitlines() if '"waiting"' not in l) + "\n")
(rdir2 / "last-state.json").write_text(json.dumps({"state": {"sessions": [{"name": "ledger-api", "question": {"asked_at": 1789210123}}]}}))
lq = json.loads(relay("push", "--dry-run").stdout)["sessions"][0]
T.check("R4 no waiting event in the ledger → asked_at = first sighting (the previous push), not now", lq["name"] == "ledger-api" and lq["question"]["asked_at"] == 1789210123, str(lq.get("question")))
(rdir2 / "last-state.json").unlink()
ledger.write_text(ledger_bak)
n_req = len(CALLS["requests"])
r = relay("push", "--dry-run")
dry = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("{") else {}
T.check("R4 push --dry-run: clear JSON on stdout, no HTTP; sessions ordered ❓ ▶ ✓ ✗ with short names, the question whole with kind ask and options 1-2, the busy session's outcome from the ledger (short = Watch line), gone from the snapshot, quota, projects with accounts from folder_map, recap, night", r.returncode == 0 and len(CALLS["requests"]) == n_req and [(x["name"], x["state"]) for x in dry.get("sessions", [])] == [("ledger-api", "waiting"), ("atlas-shop", "busy"), ("field-notes", "idle"), ("orbit-docs", "gone")] and dry["sessions"][0]["question"]["text"] == "Deploy ready, waiting for the client ok. Deploy now?" and dry["sessions"][0]["question"]["kind"] == "ask" and [o["label"] for o in dry["sessions"][0]["question"]["options"]] == ["yes", "no"] and dry["sessions"][0]["question"]["asked_at"] == 1789210500 and dry["sessions"][0]["followed"] is True and dry["sessions"][0]["project"] == "work/clients/ledger-api" and dry["sessions"][1]["outcome"]["short"] == "Migrazioni applicate, test verdi" and dry["sessions"][1]["turn_started"] == 1789210700 and dry["sessions"][1]["next"] == "Review the seeds and the admin page" and dry["sessions"][3]["since"] == S.epoch("2026-09-12T09:00:00") and dry["quota"]["work"] == {"h5": None, "w7": 75, "reset_w7": 1789444800, "reset_h5": 1789225200, "stale": True, "kind": "work"} and {(p["name"], p["account"]) for p in dry["projects"]} == {("atlas-shop", "personal"), ("field-notes", "personal"), ("ledger-api", "work"), ("orbit-docs", "work")} and dry["host"] == "crostini-test" and dry["night"] == {"queued": 0, "running": None, "items": []} and dry["v"] == 1, r.stdout[:600] + r.stderr)
T.check("R4 (1.1) every live session carries icon (from cm-color's registry, stable) and color «#RRGGBB»; the gone one has none on the first push", all(x["icon"] and re.match(r"^#[0-9A-F]{6}$", x["color"] or "") for x in dry["sessions"] if x["state"] != "gone") and dry["sessions"][3]["icon"] is None, str([(x["name"], x["icon"], x["color"]) for x in dry["sessions"]]))
r = relay("push")
T.check("R4 push: exit 0, /state on the bus is {v:1, enc} and decrypts to the same document as the dry-run (but ts)", r.returncode == 0 and set(STORE.get("state", {})) == {"v", "enc"} and STORE["state"]["v"] == 1 and {kk: v for kk, v in C.decrypt(STORE["state"], k).items() if kk != "ts"} == {kk: v for kk, v in dry.items() if kk != "ts"}, r.stdout + r.stderr + str(STORE.get("state"))[:100])
evs = {kk: C.decrypt(v, k) for kk, v in list((STORE.get("events") or {}).items())}
T.check("R4 first push: /events has launched ×3 and the question (encrypted, key <ts>_<seq>); FCM sent one data message per event with kind and session; last-state.json written", sorted(e["kind"] for e in evs.values()) == ["launched", "launched", "launched", "question"] and all(kk == evs[kk]["key"] for kk in evs) and len(CALLS["fcm"]) == 4 and any(m["message"]["data"]["kind"] == "question" and m["message"]["data"]["session"] == "ledger-api" and m["message"]["topic"] == "watch" for m in CALLS["fcm"]) and (rdir2 / "last-state.json").is_file(), str(evs) + str(CALLS["fcm"])[:300])
n_fcm, n_ev = len(CALLS["fcm"]), len(STORE["events"])
ts1 = C.decrypt(STORE["state"], k)["ts"]
time.sleep(1.1)
r = relay("push")
T.check("R4 second push without changes: new ts, no new event, no FCM", r.returncode == 0 and C.decrypt(STORE["state"], k)["ts"] > ts1 and len(STORE["events"]) == n_ev and len(CALLS["fcm"]) == n_fcm, r.stdout + r.stderr)
# la domanda risposta altrove → evento answered; una sessione sparisce → gone
rows_alive("atlas-shop", "field-notes", **{"atlas-shop": {"status": "idle"}})
(state_dir / "waiting" / "S-L").unlink()
r = relay("push")
evs = {kk: C.decrypt(v, k) for kk, v in list(STORE["events"].items())}
new = [e for e in evs.values() if e["key"] not in [] and e["ts"] >= ts1]
T.check("R4 third push: ledger-api vanished → gone; the FCM for it", any(e["kind"] == "gone" and e["session"] == "ledger-api" for e in evs.values()) and CALLS["fcm"][-1]["message"]["data"]["kind"] == "gone", str([(e["kind"], e["session"]) for e in evs.values()]))
# async con debounce: due richieste in mezzo secondo → una sola scrittura di /state
n_put = len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])
t0 = time.time(); r1 = relay("push", "--async"); r2 = relay("push", "--async"); dt = time.time() - t0
T.wait_until(lambda: len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) > n_put, 5)
time.sleep(1.5)
T.check("R4 push --async: returns at once (< 2 s for two calls), one /state write for two requests within the debounce", r1.returncode == 0 and r2.returncode == 0 and dt < 2 and len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) == n_put + 1, f"dt={dt:.2f} puts={len([x for x in CALLS['requests'] if x == ('PUT', '/state.json')]) - n_put}")
# oltre 8 KB: molti progetti → fit_state
for i in range(150):
    (ws / "personal" / f"progetto-con-un-nome-lungo-{i:03d}").mkdir()
r = relay("push", "--dry-run")
big = json.loads(r.stdout)
T.check("R4 a state over 8 KB is trimmed under the cap (projects cut to 10), the question untouched", r.returncode == 0 and S.size_of(big) <= 8192 and len(big["projects"]) <= 10, str(S.size_of(big)))
import shutil
for i in range(150):
    shutil.rmtree(ws / "personal" / f"progetto-con-un-nome-lungo-{i:03d}")
write_cfg(enabled=False)
r = relay("push")
T.check("R4 relay.enabled=false: push exits 2 saying so; --dry-run still works", r.returncode == 2 and "spento" in r.stdout and relay("push", "--dry-run").returncode == 0, r.stdout + r.stderr)
write_cfg()

# crontab finto (pair e install lo cercano prima di fare qualsiasi cosa, 0.4.20)
cron = tmp / "crontab"; cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
ENV["CM_CRONTAB_CMD"] = str(fake_crontab)

# R5: pair con un orologio finto (codice) e un telefono finto (QR, 1.15)
def pair_run(timeout=8, *extra):
    """Lancia `pair`, legge stdout fino alla riga del codice; torna (processo, codice, righe prima del codice)."""
    pr = subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--timeout", str(timeout), *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    import re as _re
    head = []
    for _ in range(200):
        line = pr.stdout.readline()
        if not line:
            break
        m = _re.search(r"Codice di pairing.*\b(\d{6})\b", line)
        if m:
            return pr, m.group(1), head
        head.append(line.rstrip("\n"))
    return pr, "", head


def pair_nodes(code):
    """I nodi di questo giro: quelli con la stessa pc_pub del codice (una conferma `ok` di un giro vecchio non conta)."""
    pub = ((STORE.get("pair") or {}).get(code) or {}).get("pc_pub")
    return {n: v for n, v in (STORE.get("pair") or {}).items() if isinstance(v, dict) and pub and v.get("pc_pub") == pub}


def pair_stop(pr):
    """Ctrl-C al pair in corso: il suo `finally` toglie i nodi dal bus."""
    import signal as _sig
    pr.send_signal(_sig.SIGINT); pr.wait(timeout=10)


pr, code, head = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
nodes = pair_nodes(code)
pid_node = next((n for n in nodes if n != code), "")
T.check("R5 pair: a six-digit code on stdout; /pair/<code> has the PC's public key, host and exp", len(code) == 6 and nodes.get(code, {}).get("pc_pub") and nodes[code]["host"] == "crostini-test" and nodes[code]["exp"] > time.time(), str(STORE.get("pair")))
T.check("R5 (1.15) the same {pc_pub, host, exp} under /pair/<id>, the id 22 base64url characters; without the app's Firebase data no QR, said in one line above the code", len(pid_node) == 22 and re.fullmatch(r"[A-Za-z0-9_-]{22}", pid_node) and nodes[pid_node] == nodes[code] and len(nodes) == 2 and any("niente QR" in l and "relay.firebase_app" in l for l in head), f"nodes={list(nodes)} head={head}")
w_priv, w_pub = C.pair_keys()
k_watch = C.shared_key(w_priv, STORE["pair"][code]["pc_pub"])
for i in range(3):   # tre sul codice, due sull'id: i tentativi sono in comune
    http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w_pub, "uid": "u-bad", "name": "intruder", "check": f"{i:016x}"})
    T.wait_until(lambda: not (STORE.get("pair") or {}).get(code, {}).get("watch"), 3)
for i in range(2):
    http("PUT", f"/pair/{pid_node}/watch.json", {"watch_pub": w_pub, "uid": "u-bad", "name": "intruder", "check": f"{i:016x}"})
    T.wait_until(lambda: not (STORE.get("pair") or {}).get(pid_node, {}).get("watch"), 3)
pr.wait(timeout=10)
T.check("R5 five wrong checks (three on the code, two on the id: attempts shared) → exit 2, no key change, no uid allowed, both nodes cleaned", pr.returncode == 2 and C.load_key(rdir2) == k and not (STORE.get("allowed") or {}) and code not in (STORE.get("pair") or {}) and pid_node not in (STORE.get("pair") or {}), f"rc={pr.returncode} " + pr.stdout.read() + pr.stderr.read())
pr, code, head = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
w_priv, w_pub = C.pair_keys()
k_watch = C.shared_key(w_priv, STORE["pair"][code]["pc_pub"])
http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w_pub, "uid": "u1", "name": "watch-pixel5", "check": C.check_code(k_watch, code)})
pr.wait(timeout=10)
out5 = pr.stdout.read()
T.wait_until(lambda: set((STORE.get("pair") or {}).get(code, {})) == {"ok"}, 3)
T.check("R5 right check on the code → exit 0, the shared key saved (0600) and equal to the watch's, u1 in /allowed and devices.json, only the confirmation left under /pair/<code> (the watch still has to read it), /pair/<id> gone, «accoppiato» printed", pr.returncode == 0 and C.load_key(rdir2) == k_watch and STORE.get("allowed") == {"u1": True} and json.loads((rdir2 / "devices.json").read_text())["u1"]["name"] == "watch-pixel5" and set((STORE.get("pair") or {}).get(code, {})) == {"ok"} and STORE["pair"][code]["ok"]["check"] == C.check_code(k_watch, code + ":pc") and pid_node not in STORE["pair"] and "accoppiato: watch-pixel5 (uid u1)" in out5, f"rc={pr.returncode} {out5} {pr.stderr.read()} {STORE.get('allowed')} {STORE.get('pair')}")
# 1.15: il telefono risponde sull'id del QR, con uids e names: tutti gli uid in /allowed, i nomi in devices.json
pr, code, head = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
w2_priv, w2_pub = C.pair_keys(); k2 = C.shared_key(w2_priv, STORE["pair"][pid_node]["pc_pub"])
http("PUT", f"/pair/{pid_node}/watch.json", {"watch_pub": w2_pub, "uid": "u2", "name": "Pixel 9", "check": C.check_code(k2, pid_node),
                                              "uids": ["u2", "u3"], "names": {"u2": "Pixel 9", "u3": "Pixel Watch 5"}})
pr.wait(timeout=10); out5b = pr.stdout.read()
dev = json.loads((rdir2 / "devices.json").read_text())
T.check("R5 (1.15) pairing again from the phone on /pair/<id> with uids and names → exit 0, /allowed = {u2, u3} (u1 revoked), devices.json with both names, new key, ok.check = HMAC(key, id + «:pc») under /pair/<id>, /pair/<code> gone and the previous confirmation swept", pr.returncode == 0 and STORE.get("allowed") == {"u2": True, "u3": True} and {u: d["name"] for u, d in dev.items()} == {"u2": "Pixel 9", "u3": "Pixel Watch 5"} and C.load_key(rdir2) == k2 and set(STORE["pair"]) == {pid_node} and STORE["pair"][pid_node] == {"ok": {"host": "crostini-test", "check": C.check_code(k2, pid_node + ":pc")}} and "Pixel 9 (uid u2)" in out5b and "Pixel Watch 5 (uid u3)" in out5b, f"rc={pr.returncode} {out5b} {STORE.get('allowed')} {STORE.get('pair')} {dev}")
pr, code, head = pair_run(2)
pr.wait(timeout=10)
T.check("R5 nobody answers → exit 3 at the timeout, both nodes cleaned", pr.returncode == 3 and not pair_nodes(code), f"rc={pr.returncode} {STORE.get('pair')}")
# con i dati dell'app Firebase: --text stampa il JSON del QR su una riga (contratto 1.15, pair-qr.json), poi il codice
APP = {"api_key": "AIzaSyD-test-key", "project_id": "fake-project", "app_id": "1:123456789012:android:0a1b2c3d4e5f6a7b"}
write_cfg(firebase_app=APP)
pr, code, head = pair_run(8, "--text")
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
try:
    qr_doc = json.loads(head[-1]) if head else {}
except ValueError:
    qr_doc = {}
T.check("R5 (1.15) pair --text: one JSON line {v:1, i:<id node>, c:pc_pub, h, e:exp, f:{k,p,a,d,t}} above the code, no QR drawn", len(head) == 1 and qr_doc.get("v") == 1 and qr_doc.get("i") == pid_node and qr_doc.get("c") == STORE["pair"][pid_node]["pc_pub"] and qr_doc.get("h") == "crostini-test" and qr_doc.get("e") == STORE["pair"][pid_node]["exp"] and qr_doc.get("f") == {"k": APP["api_key"], "p": APP["project_id"], "a": APP["app_id"], "d": URL, "t": "watch"}, str(head)[:400])
pair_stop(pr)
T.wait_until(lambda: code not in (STORE.get("pair") or {}) and pid_node not in (STORE.get("pair") or {}), 3)
# senza --text: il QR a mezzi blocchi, margine di 2 moduli, decodificato (OpenCV, se c'e') = quel JSON
pr, code, head = pair_run(8)
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
pid_node = next(n for n in pair_nodes(code) if n != code)
rows = [l for l in head if l and set(l) <= set("█▀▄ ")]


def qr_decode(rows, scale=6):
    """Le righe a mezzi blocchi tornano immagine (chiaro = blocco) e OpenCV le legge; None senza cv2. Il detector e'
    QRCodeDetectorAruco (OpenCV >= 4.8): quello classico perde circa un QR su dieci a qualunque scala e livello di
    correzione, sempre sugli stessi contenuti (misurato su 40 payload di pair, 25/09), e il test era aleatorio."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    Det = getattr(cv2, "QRCodeDetectorAruco", None) or cv2.QRCodeDetector
    img = np.zeros((len(rows) * 2 * scale, len(rows[0]) * scale), np.uint8)
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            img[y * 2 * scale:(y * 2 + 1) * scale, x * scale:(x + 1) * scale] = 255 if ch in "█▀" else 0
            img[(y * 2 + 1) * scale:(y * 2 + 2) * scale, x * scale:(x + 1) * scale] = 255 if ch in "█▄" else 0
    return Det().detectAndDecode(img)[0]


decoded = qr_decode(rows) if rows else ""
T.check("R5 (1.15) pair draws the QR in half blocks: «Inquadra» line, square rows of equal width, a light margin of 2 modules (1 row and 2 columns of full blocks) on every side, then the code", any("Inquadra il QR" in l for l in head) and len(rows) >= 20 and len({len(r) for r in rows}) == 1 and rows[0] == "█" * len(rows[0]) and rows[-1] == "█" * len(rows[0]) and rows[1] != rows[0] and all(r.startswith("██") and r.endswith("██") for r in rows) and abs(len(rows) * 2 - len(rows[0])) <= 1, f"rows={len(rows)} width={len(rows[0]) if rows else 0} head={head[:3]}")
T.check("R5 (1.15) the drawn QR decodes (OpenCV) to the JSON of the QR, with i = the id node on the bus (skipped without cv2)", decoded is None or (decoded and json.loads(decoded)["i"] == pid_node and json.loads(decoded)["c"] == STORE["pair"][pid_node]["pc_pub"]), f"decoded={str(decoded)[:120]}")
pair_stop(pr)
T.wait_until(lambda: code not in (STORE.get("pair") or {}) and pid_node not in (STORE.get("pair") or {}), 3)
write_cfg()

# R5b: i vettori del contratto 1.15 — pair-qr.json e pair-response.json (chiave privata del PC = scalare 0..31,
# del telefono = 32..63): pair_accept sull'id della fixture accetta la risposta e produce esattamente `ok`
os.environ.update({"CLAUDE_MASTER_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
RL = load("cm-relay")
from cryptography.hazmat.primitives.asymmetric import x25519 as _x
from cryptography.hazmat.primitives import serialization as _ser
FQ = json.loads((FIX / "pair-qr.json").read_text()); FR = json.loads((FIX / "pair-response.json").read_text())
pc_priv = _x.X25519PrivateKey.from_private_bytes(bytes(range(32)))
ph_priv = _x.X25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
pc_pub_fx = base64.b64encode(pc_priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)).decode()
k_fx = C.shared_key(ph_priv, FQ["c"])
T.check("R5b fixture pair-qr.json: c is the public key of scalar 0..31, i has 22 base64url chars, v = 1, f has k p a d t", FQ["c"] == pc_pub_fx and len(FQ["i"]) == 22 and FQ["v"] == 1 and set(FQ["f"]) == {"k", "p", "a", "d", "t"}, FQ["c"])
T.check("R5b fixture pair-response.json: watch_pub is the public key of scalar 32..63 and check = HMAC(key, i) of the shared key", FR["watch"]["watch_pub"] == base64.b64encode(ph_priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)).decode() and FR["watch"]["check"] == C.check_code(k_fx, FQ["i"]), FR["watch"]["check"])
got = RL.pair_accept(pc_priv, FQ["i"], FR["watch"], FQ["h"])
T.check("R5b pair_accept(id of the fixture, watch of pair-response.json) → the same key, exactly pair-response.json's `ok`, the devices with uids and names", got is not None and got[0] == k_fx and got[1] == FR["ok"] and {u: d["name"] for u, d in got[2].items()} == FR["watch"]["names"] and list(got[2]) == FR["watch"]["uids"], str(got[1:] if got else got))
T.check("R5b pair_accept with the check computed on the code instead of the id → refused (the check binds the node)", RL.pair_accept(pc_priv, "123456", FR["watch"], FQ["h"]) is None, "")
pl = RL.qr_payload(FQ["i"], FQ["c"], FQ["h"], FQ["e"], {"api_key": FQ["f"]["k"], "project_id": FQ["f"]["p"], "app_id": FQ["f"]["a"]})
T.check("R5b qr_payload with the fixture's values = pair-qr.json (d and t from this config: firebase_url and fcm_topic)", pl == dict(FQ, f=dict(FQ["f"], d=URL, t="watch")), json.dumps(pl))
fx_rows = RL.qr_lines(json.dumps(FQ, ensure_ascii=False, separators=(",", ":")))
fx_dec = qr_decode(fx_rows)
T.check("R5b the fixture's JSON drawn as QR and decoded (OpenCV) gives back the same document (skipped without cv2)", fx_dec is None or (fx_dec and json.loads(fx_dec) == FQ), str(fx_dec)[:100])
for kk in ("CLAUDE_MASTER_CONFIG", "HOME", "CM_HOME"):
    os.environ.pop(kk, None)

# R5c (1.15): prove isolate — con CLAUDE_MASTER_CONFIG su una configurazione di prova (relay.dir, service account e
# firebase_url suoi) pair, push e serve lavorano solo li': chiave, devices.json, /allowed e crontab della
# configurazione principale restano come sono (le prove dell'app non devono scollegare l'orologio vero)
URL_ISO, CALLS_ISO, STORE_ISO = T.fake_rtdb("iso-project")
rdir_iso = tmp / "relay-iso"
SA_ISO = tmp / "sa-iso.json"; SA_ISO.write_text(SA.read_text().replace("fake-project", "iso-project"))
cfg_iso = tmp / "config-iso.json"
cfg_iso.write_text(json.dumps(dict(json.loads(cfg.read_text()), relay={"enabled": True, "firebase_url": URL_ISO, "service_account": str(SA_ISO), "token_url": URL_ISO + "/token",
                                                                         "fcm_url": URL_ISO, "dir": str(rdir_iso), "host": "iso-host", "debounce_s": 1, "fcm_topic": "watch-iso", "firebase_app": APP})))
ENV_ISO = dict(ENV, CLAUDE_MASTER_CONFIG=str(cfg_iso))
snap = lambda: ((rdir2 / "key").read_bytes(), (rdir2 / "devices.json").read_bytes(), json.dumps(STORE.get("allowed"), sort_keys=True), cron.read_text(), (rdir2 / "serve.pid").exists())  # noqa: E731
before_iso = snap()
pr = subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--timeout", "8", "--text"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV_ISO)
T.wait_until(lambda: any(isinstance(v, dict) and v.get("pc_pub") for v in (STORE_ISO.get("pair") or {}).values()), 4)
iso_nodes = {n: v for n, v in (STORE_ISO.get("pair") or {}).items() if isinstance(v, dict) and v.get("pc_pub")}
iso_id = next((n for n in iso_nodes if len(n) == 22), "")
wi_priv, wi_pub = C.pair_keys(); k_iso = C.shared_key(wi_priv, iso_nodes[iso_id]["pc_pub"]) if iso_id else b""
if iso_id:
    import urllib.request as _ur
    req = _ur.Request(f"{URL_ISO}/pair/{iso_id}/watch.json", data=json.dumps({"watch_pub": wi_pub, "uid": "u-iso", "name": "Phone iso", "check": C.check_code(k_iso, iso_id)}).encode(), method="PUT", headers={"Content-Type": "application/json"})
    _ur.urlopen(req, timeout=5).read()
pr.wait(timeout=12); out_iso = pr.stdout.read()
r_push = relay("push", env=ENV_ISO)
r_ens = relay("ensure", env=ENV_ISO)
iso_pid = int((rdir_iso / "serve.pid").read_text().strip() or 0) if (rdir_iso / "serve.pid").exists() else 0
r_off = relay("off", env=ENV_ISO)
T.check("R5c the trial config pairs on its own bus and dir: exit 0, key and devices.json under its relay.dir, u-iso in ITS /allowed, its /state pushed (exit 0), its serve started and stopped", pr.returncode == 0 and C.load_key(rdir_iso) == k_iso and "u-iso" in json.loads((rdir_iso / "devices.json").read_text()) and STORE_ISO.get("allowed") == {"u-iso": True} and r_push.returncode == 0 and "enc" in (STORE_ISO.get("state") or {}) and r_ens.returncode == 0 and iso_pid > 0 and r_off.returncode == 0, f"rc={pr.returncode} {out_iso[-200:]} {pr.stderr.read()[-200:]} push={r_push.returncode} {r_push.stderr[-200:]} ens={r_ens.stdout}{r_ens.stderr} pid={iso_pid}")
T.check("R5c meanwhile the main config is untouched: same key bytes, same devices.json, same /allowed on its bus, same crontab, no serve pid in its dir", snap() == before_iso and json.loads((rdir2 / "devices.json").read_text()) == dev and STORE.get("allowed") == {"u2": True, "u3": True}, f"before={before_iso[2:]} after={snap()[2:]}")
T.wait_until(lambda: not (rdir_iso / "serve.pid").exists() or not os.path.exists(f"/proc/{iso_pid}"), 5)
k = k2   # da qui la chiave viva e' quella dell'ultimo pairing

# R6: serve — i comandi del contratto
rows_alive("ledger-api", "atlas-shop", "field-notes")
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
relay("push")


def serve_pid():
    try:
        pid = int((rdir2 / "serve.pid").read_text().strip() or 0); os.kill(pid, 0); return pid
    except (OSError, ValueError):
        return 0


r = relay("ensure")
T.check("R6 relay ensure starts serve (pidfile, live process); a second ensure keeps the same pid", r.returncode == 0 and serve_pid() > 0 and relay("ensure").returncode == 0 and serve_pid() == serve_pid(), r.stdout + r.stderr)
T.check("R6 the status file is written at startup with the new pid and zeroed counters (else `status` shows the dead process's numbers)", T.wait_until(lambda: json.loads((rdir2 / "serve.json").read_text()).get("pid") == serve_pid(), 5) and json.loads((rdir2 / "serve.json").read_text())["reconnects"] == 0, (rdir2 / "serve.json").read_text() if (rdir2 / "serve.json").exists() else "assente")
pid1 = serve_pid()
CMDS = json.loads((FIX / "cmd-result-sample.json").read_text())["cmd"]


def send_cmd(cmd, wait=20):   # sotto carico il daemon impiega di piu' (build in parallelo)
    cid = cmd["id"]
    http("PUT", f"/cmd/{cid}.json", C.encrypt(cmd, k))
    T.wait_until(lambda: (STORE.get("result") or {}).get(cid), wait)
    res = STORE.get("result", {}).get(cid)
    return C.decrypt(res, k) if res else None


n_state_puts = len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])
res = send_cmd(CMDS[0])   # answer ledger-api 1
T.check("R6 answer → `answer work-ledger-api 1` (name mapped to tmux), /result {ok, text «answered 1. yes», at}, /cmd/<id> deleted", res and res["ok"] is True and res["text"] == "answered 1. yes" and isinstance(res["at"], int) and "answer work-ledger-api 1" in cm_calls() and T.wait_until(lambda: CMDS[0]["id"] not in (STORE.get("cmd") or {}), 5), str(res) + str(cm_calls()[-4:]))   # il daemon scrive /result e POI cancella /cmd: si aspetta
T.check("R6 a successful answer removes the hook's waiting flag (else «waiting» until the next prompt: answered and outcome 3 min late, 14/09)", not (state_dir / "waiting" / "S-L").exists(), str(list((state_dir / "waiting").iterdir())))
led_rows = lambda: [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]  # noqa: E731
T.check("R6 the command is annotated in the ledger: event watch-cmd with op, name, by (who answered) and ok", any(x.get("event") == "watch-cmd" and x.get("op") == "answer" and x.get("name") == "ledger-api" and x.get("by") == "watch-pixel5" and x.get("ok") is True for x in led_rows()), str([x for x in led_rows() if x.get("event") == "watch-cmd"][-2:]))
T.check("R6 after a command /state is republished (a new PUT of /state, from the push in background)", T.wait_until(lambda: len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) > n_state_puts, 30), "")
res = send_cmd(CMDS[1])   # prompt atlas-shop
T.check("R6 prompt → talk atlas-shop with the watch prefix (Watch: line requested) --no-wait, «delivered», atlas-shop awaiting", res and res["ok"] and res["text"] == "delivered" and any(c.startswith("talk atlas-shop Dall'utente via polso") and "Watch:" in c and c.endswith("review the test seeds --no-wait") for c in cm_calls()) and "atlas-shop" in json.loads((rdir2 / "awaiting.json").read_text()), str(res) + str(cm_calls()[-3:]))
res = send_cmd(CMDS[2])   # launch path fuori dai progetti pubblicati
T.check("R6 launch of a path outside the published projects → ok false, no launch", res and res["ok"] is False and "not a published project" in res["text"] and not any(c.startswith("launch ") for c in cm_calls()), str(res))
res = send_cmd(dict(CMDS[2], id="6f1c2d3e-0003-4000-8000-0000000000aa", arg=str(ws / "work" / "own" / "orbit-docs")))
T.check("R6 launch of a published project → `launch PATH --window` (its tab on the desktop, 1.9.2), «launched orbit-docs (work)»", res and res["ok"] is True and res["text"] == "launched orbit-docs (work)" and any(c.startswith("launch ") and c.endswith("orbit-docs --window") for c in cm_calls()), str(res) + str(cm_calls()[-3:]))
# 1.4: «last» — l'ultimo messaggio dell'assistente per intero dal transcript (il ledger ne tiene solo la coda)
tdir_a = home / ".claude" / "projects" / "".join(ch if ch.isalnum() else "-" for ch in os.path.realpath(str(ws / "personal" / "atlas-shop")))
tdir_a.mkdir(parents=True, exist_ok=True)
lungo = "Prima frase del messaggio lungo. " + ("Dettaglio del lavoro fatto. " * 200) + "Ultima frase."
(tdir_a / "S-A.jsonl").write_text("\n".join([
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Messaggio vecchio da ignorare."}]}}),
    json.dumps({"type": "user", "message": {"role": "user", "content": "fai"}}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": lungo}, {"type": "tool_use", "name": "Bash", "input": {"command": "x"}}]}}),
]) + "\n")
res = send_cmd({"id": "6f1c2d3e-0010-4000-8000-000000000010", "op": "last", "session": "atlas-shop", "arg": None, "issued": 1, "by": "watch-pixel5"})
T.check("R6 (1.4) last → the whole last assistant message from the transcript, up to 4000 chars cut at a sentence end (the ledger only keeps 600 of the tail)", res and res["ok"] is True and res["text"].startswith("Prima frase del messaggio lungo.") and len(res["text"]) <= 4000 and len(res["text"]) > 3000 and res["text"].rstrip().endswith(".") and "Messaggio vecchio" not in res["text"], str(len(res["text"] if res else "")) + " " + (res or {}).get("text", "")[:60])
(tdir_a / "S-A.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Corto e intero."}]}}) + "\n")
res = send_cmd({"id": "6f1c2d3e-0011-4000-8000-000000000011", "op": "last", "session": "atlas-shop", "arg": None, "issued": 1, "by": "watch-pixel5"})
T.check("R6 (1.4) a short message comes back whole, untouched", res and res["ok"] and res["text"] == "Corto e intero.", str(res))
res = send_cmd({"id": "6f1c2d3e-0012-4000-8000-000000000012", "op": "last", "session": "field-notes", "arg": None, "issued": 1, "by": "watch-pixel5"})
T.check("R6 (1.4) a session without a transcript → ok false with «nessun messaggio da leggere»", res and res["ok"] is False and "nessun messaggio" in res["text"], str(res))
res = send_cmd(CMDS[3])   # screen
T.check("R6 screen → `screen atlas-shop --lines 30 --join` (tmux reunites wrapped lines: no words cut on the wrist), 30 lines in text", res and res["ok"] and len(res["text"].splitlines()) == 30 and "screen atlas-shop --lines 30 --join" in cm_calls(), str(res)[:200] + str([c for c in cm_calls() if c.startswith("screen ")]))
res = send_cmd(CMDS[4])   # follow
T.check("R6 follow → follow.json has atlas-shop, «following atlas-shop»; the next /state marks it followed", res and res["ok"] and res["text"] == "following atlas-shop" and "atlas-shop" in json.loads((rdir2 / "follow.json").read_text()) and T.wait_until(lambda: any(s_["name"] == "atlas-shop" and s_["followed"] for s_ in C.decrypt(STORE["state"], k)["sessions"]), 30), str(res))
res = send_cmd(dict(CMDS[4], id="6f1c2d3e-0005-4000-8000-0000000000bb", op="unfollow"))
T.check("R6 unfollow → removed", res and res["ok"] and "atlas-shop" not in json.loads((rdir2 / "follow.json").read_text()), str(res))
res = send_cmd(CMDS[5])   # resume orbit-docs (gone)
T.check("R6 resume of a gone session → ok false «orbit-docs is gone: use launch»", res and res["ok"] is False and res["text"] == "orbit-docs is gone: use launch", str(res))
# R6 (1.9, 15/09, dall'orologio: una sessione chiusa non si poteva riprendere): reopen rilancia una gone
orbit = ws / "work" / "own" / "orbit-docs"


def reopen_cmd(n, session="orbit-docs"):
    return {"id": f"6f1c2d3e-0010-4000-8000-0000000001{n:02d}", "op": "reopen", "session": session, "arg": None, "issued": 1789210960, "by": "watch-pixel5"}


def launches():
    return [c for c in cm_calls() if c.startswith("launch ")]


res = send_cmd(reopen_cmd(1))
T.check("R6 reopen of a gone session, no conversation id, nobody live in its folder → `launch <path> --window --account work --continue`, «reopened orbit-docs (work): last conversation in the folder»", res and res["ok"] is True and res["text"] == "reopened orbit-docs (work): last conversation in the folder" and launches()[-1] == f"launch {orbit} --window --account work --continue", str(res) + str(cm_calls()[-3:]))
T.check("R6 reopen is annotated in the ledger (watch-cmd, op reopen, ok)", any(x.get("event") == "watch-cmd" and x.get("op") == "reopen" and x.get("name") == "orbit-docs" and x.get("ok") is True for x in led_rows()), "")
tdir = home / ".claude-pixel" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(orbit))
tdir.mkdir(parents=True, exist_ok=True)
(tdir / "S-O.jsonl").write_text("{}\n")
good_json.write_text(json.dumps({"sessioni": [dict(GOOD_ORBIT, session_id="S-O")]}))
res = send_cmd(reopen_cmd(2))
T.check("R6 reopen with the snapshot's conversation id and its transcript on disk → `--resume S-O` (never -c), «…: same conversation»", res and res["ok"] is True and res["text"] == "reopened orbit-docs (work): same conversation" and launches()[-1] == f"launch {orbit} --window --account work --resume S-O", str(res) + str(launches()[-1:]))
good_json.write_text(json.dumps({"sessioni": [dict(GOOD_ORBIT, session_id="S-GONE")]}))
rows_alive("ledger-api", "atlas-shop", "field-notes", **{"field-notes": {"cwd": str(orbit)}})
n_l = len(launches())
res = send_cmd(reopen_cmd(3))
T.check("R6 reopen with an id whose transcript is missing and another live session in the folder → ok false, no launch (-c would take the other one's conversation)", res and res["ok"] is False and res["text"] == "orbit-docs: conversation unknown and 1 live session(s) in its folder: use launch" and len(launches()) == n_l, str(res))
rows_alive("ledger-api", "atlas-shop", "field-notes")
res = send_cmd(reopen_cmd(4, "atlas-shop"))
T.check("R6 reopen of a live name → ok false «atlas-shop is already running», no launch", res and res["ok"] is False and res["text"] == "atlas-shop is already running" and len(launches()) == n_l, str(res))
res = send_cmd(reopen_cmd(5, "nope"))
T.check("R6 reopen of a name outside the registry snapshot → ok false «nope is not a closed session»", res and res["ok"] is False and res["text"] == "nope is not a closed session" and len(launches()) == n_l, str(res))
good_json.write_text(json.dumps({"sessioni": [GOOD_ORBIT]}))
launch_adds.write_text(json.dumps(json.loads(alive.read_text()) + [{"pid": 11, "name": "work-orbit-docs-2", "tmux": "work-orbit-docs-2", "cwd": str(orbit), "status": "idle", "waiting": False, "link": "", "account": "work", "session_id": "S-O2", "attached": False, "started_at": 1789211000000}]))
res = send_cmd(reopen_cmd(6))
T.check("R6 reopen that comes back under another name (launch takes the first free one) → the old name leaves the snapshot (`registry --closed work-orbit-docs`), else it stays ✗ for ever", res and res["ok"] is True and "registry --closed work-orbit-docs" in cm_calls(), str(res) + str(cm_calls()[-4:]))
launch_adds.unlink()
rows_alive("ledger-api", "atlas-shop", "field-notes")
# una domanda nuova su ledger-api: la risposta di prima ha tolto il flag, l'hook del dialogo nuovo lo riscrive
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
relay("push")
res = send_cmd(CMDS[6])   # allow_all ledger-api
T.check("R6 allow_all without a «don't ask again» option → ok false with the contract's text", res and res["ok"] is False and res["text"] == "no «don't ask again» option on this question", str(res))
# R6 (1.10, 15/09, da claude-master-watch): answer con «text:<testo>» e «chat»
# R10 (contratto 1.12, 16/09): modello ed effort dal polso, solo per la sessione, via `claude-master model|effort`;
# il testo di un rifiuto e' la prima riga del comando, breve, così com'è
RES = json.loads((FIX / "cmd-result-sample.json").read_text())["result"]
res = send_cmd(CMDS[9])   # model field-notes claude-sonnet-5
T.check("R10 (1.12) model → `model field-notes claude-sonnet-5`, /result ok with the command's line (as in the fixture)",
        res and res["ok"] is True and res["text"] == RES[9]["text"] and "model field-notes claude-sonnet-5" in cm_calls(), str(res) + str(cm_calls()[-3:]))
res = send_cmd(CMDS[10])   # effort atlas-shop low: occupata
T.check("R10 (1.12) effort on a busy session → /result ok=false with the short reason, as the watch shows it",
        res and res["ok"] is False and res["text"] == RES[10]["text"], str(res))
r = relay("push", "--dry-run"); dry10 = json.loads(r.stdout)
T.check("R10 (1.12) /state carries choices: full model ids (as in session.model.id) with labels, and the five efforts",
        dry10.get("choices") == json.loads((FIX / "state-1-question.json").read_text())["choices"], str(dry10.get("choices")))
# R11 (contratto 1.13, 16/09): «Nuova sessione» dal polso col primo messaggio — launch, poi talk sulla sessione NATA
# (che puo' chiamarsi diversamente dal progetto), e nel /result il suo nome come in sessions[].name
fn = ws / "personal" / "field-notes"
launch_adds.write_text(json.dumps(json.loads(alive.read_text()) + [{"pid": 12, "name": "field-notes-2", "tmux": "field-notes-2", "cwd": str(fn), "status": "idle", "waiting": False, "link": "", "account": "personal", "session_id": "S-F2", "attached": False, "started_at": 1789211000000}]))
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[11], arg=str(fn)))
calls = cm_calls()[n_calls:]
T.check("R11 (1.13) launch with text → `launch PATH --window`, then `talk` with the watch prefix to the NEW session field-notes-2, --no-wait",
        any(c.startswith("launch ") and str(fn) in c for c in calls) and any(c.startswith("talk field-notes-2 Dall'utente via polso") and "check the draft for typos" in c and c.endswith("--no-wait") for c in calls), str(calls))
T.check("R11 (1.13) /result ok with the fixture's text and `session` = field-notes-2, the name the watch will see in sessions[].name",
        res and res["ok"] is True and res["text"] == RES[11]["text"] and res.get("session") == RES[11]["session"] == "field-notes-2", str(res))
launch_adds.unlink()
# R13 (contratto 1.17, 29/09): la coda di stanotte dall'app, via `claude-master night add|remove`
led = ws / "work" / "clients" / "ledger-api"
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[12], arg=str(led)))
T.check("R13 (1.17) night_add → `night add PATH PROMPT`, /result ok with the fixture's text and `job` = the new id",
        res and res["ok"] is True and res["text"] == RES[12]["text"] and res.get("job") == RES[12]["job"] == "7b21d4e8"
        and f"night add {led} Update the changelog for 2.4 and check the migration notes" in cm_calls()[n_calls:], str(res) + str(cm_calls()[n_calls:]))
res = send_cmd(dict(CMDS[12], id="6f1c2d3e-0123-4000-8000-000000000201"))
res2 = send_cmd(dict(CMDS[12], id="6f1c2d3e-0123-4000-8000-000000000202", arg=str(led), text="   "))
res3 = send_cmd(dict(CMDS[12], id="6f1c2d3e-0123-4000-8000-000000000203", arg=str(led), text="FULL"))
T.check("R13 (1.17) night_add refusals in plain words: a folder outside the published projects, an empty prompt (no `night` run for either), a full queue",
        res and res["ok"] is False and "not a published project" in res["text"] and res2 and res2["ok"] is False and res2["text"] == "empty prompt: nothing to queue"
        and res3 and res3["ok"] is False and res3["text"] == "tonight's queue is full (8 jobs): remove one first"
        and len([c for c in cm_calls()[n_calls:] if c.startswith("night add")]) == 2, str(res) + str(res2) + str(res3))
res = send_cmd(CMDS[13])
T.check("R13 (1.17) night_remove → `night remove ID`, /result ok with the fixture's text",
        res and res["ok"] is True and res["text"] == RES[13]["text"] and "night remove 5d0e6b92" in cm_calls(), str(res))
res = send_cmd(dict(CMDS[13], id="6f1c2d3e-0124-4000-8000-000000000201", arg="deadbeef"))
res2 = send_cmd(dict(CMDS[13], id="6f1c2d3e-0124-4000-8000-000000000202", arg="a3f09c1e"))
T.check("R13 (1.17) night_remove refusals in plain words: unknown id, job already started",
        res and res["ok"] is False and res["text"] == "no job deadbeef in tonight's queue" and res2 and res2["ok"] is False and res2["text"] == "job a3f09c1e has already started: it cannot be removed", str(res) + str(res2))
nq = state_dir / "night-queue.jsonl"
nq.write_text(json.dumps({"id": "a3f09c1e", "dir": str(ws / "personal" / "atlas-shop"), "prompt": "fix the flaky tests", "account": "personal", "added": iso(1789207200), "started": 1789215000}) + "\n"
              + json.dumps({"id": "7b21d4e8", "dir": str(led), "prompt": "Update the changelog", "account": "work", "added": iso(1789210620)}) + "\n")
r = relay("push", "--dry-run"); dryn = json.loads(r.stdout)["night"]
nq.unlink()
T.check("R13 (1.17) push reads the queue file: queued 2, running = the started job's folder name, items in file order with started as epoch",
        dryn["queued"] == 2 and dryn["running"] == "atlas-shop" and [(i["id"], i["name"], i["started"]) for i in dryn["items"]] == [("a3f09c1e", "atlas-shop", 1789215000), ("7b21d4e8", "ledger-api", None)] and dryn["items"][0]["added"] == 1789207200, str(dryn))
# R14 (contratto 1.18, 29/09): diario, resoconto della notte e ripresa della quota anche all'app, come eventi con la sveglia
def new_events(kind, before):
    return [C.decrypt(v, k) for kk, v in sorted((STORE.get("events") or {}).items()) if kk not in before and C.decrypt(v, k)["kind"] == kind]


today = time.strftime("%Y-%m-%d")
before, n_fcm = set(STORE.get("events") or {}), len(CALLS["fcm"])
seq0 = int(json.loads((rdir2 / "last-state.json").read_text()).get("seq") or 0)
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--send"], capture_output=True, text=True, env=ENV, timeout=120)
ev = new_events("recap", before)
T.check("R14 (1.18) recap --send → one `recap` event: title «Diario del dd/mm», body = the plain diary as printed (no Telegram HTML), ref = the day, key after the push seq (a push in background may take a number first); FCM with kind recap",
        r.returncode == 0 and len(ev) == 1 and ev[0]["title"] == "Diario del " + time.strftime("%d/%m") and ev[0]["ref"] == today and "<b>" not in ev[0]["body"]
        and ev[0]["body"].splitlines()[0] == r.stdout.splitlines()[0] and int(ev[0]["key"].split("_")[1]) > seq0 and ev[0]["session"] is None
        and set(ev[0]) == set(EV[0]) and CALLS["fcm"][n_fcm:] and CALLS["fcm"][-1]["message"]["data"]["kind"] == "recap", r.stdout[-300:] + r.stderr[-300:] + str(ev))
nq = state_dir / "night-queue.jsonl"
nq.write_text(json.dumps({"id": "c0ffee01", "dir": str(ws / "personal" / "atlas-shop"), "prompt": "fix the flaky tests", "account": "personal", "model": "", "effort": "", "max_turns": 5, "added": iso(1789207200)}) + "\n")
before = set(STORE.get("events") or {})
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night.py"), "run", "--send"], capture_output=True, text=True, timeout=120,
                   env=dict(ENV, CM_CLAUDE_BIN=str(T.ROOT / "tests" / "lib" / "fake-claude.sh"), CM_NIGHT_FREE_MB="4000"))
ev = new_events("night_report", before)
T.check("R14 (1.18) night run --send → one `night_report` event: title «Notte: 1 lavori, 1 riusciti», body = the Telegram summary, ref = today",
        r.returncode == 0 and len(ev) == 1 and ev[0]["title"] == "Notte: 1 lavori, 1 riusciti" and ev[0]["ref"] == today
        and ev[0]["body"].startswith("Turno di notte: 1 lavori eseguiti") and "✓ atlas-shop" in ev[0]["body"], r.stdout[-300:] + r.stderr[-300:] + str(ev))
nq.unlink(missing_ok=True)
CORE = f"import importlib.util as u; s = u.spec_from_file_location('c', {str(T.SCRIPTS / 'cm-core.py')!r}); m = u.module_from_spec(s); s.loader.exec_module(m); "
before = set(STORE.get("events") or {})
r = subprocess.run([sys.executable, "-c", CORE + "print(m.relay_event('quota', '✓ quota personal tornata', 'reset 13:10\\n' + 'riga\\n' * 3000, account='personal'))"],
                   capture_output=True, text=True, env=ENV, timeout=60)
ev = new_events("quota", before)
T.check("R14 (1.18) relay_event quota (the guard's resume): account set, body cut at a line end within 4000 characters, no «…»",
        r.stdout.strip() == "True" and len(ev) == 1 and ev[0]["account"] == "personal" and len(ev[0]["body"]) <= 4000 and ev[0]["body"].endswith("riga") and "…" not in ev[0]["body"], r.stdout + r.stderr + str(ev)[:300])
T.check("R14 (1.18) cut_lines: whole when it fits; else whole lines only; one line longer than the cap cut at a word end",
        S.cut_lines("a\nb", 10) == "a\nb" and S.cut_lines("aaaa\nbbbb\ncccc", 10) == "aaaa\nbbbb" and S.cut_lines("one two three", 8) == "one two", S.cut_lines("aaaa\nbbbb\ncccc", 10))
write_cfg(enabled=False)
before = set(STORE.get("events") or {})
r = subprocess.run([sys.executable, "-c", CORE + "print(m.relay_event('quota', 't', 'b'))"], capture_output=True, text=True, env=ENV, timeout=60)
write_cfg()
T.check("R14 (1.18) relay off → relay_event does nothing and says so (Telegram goes on as before)", r.stdout.strip() == "False" and set(STORE.get("events") or {}) == before, r.stdout + r.stderr)
# R15 (contratto 1.19, 29/09): «Condividi» dal telefono — op report, immagine cifrata in /share/<id>, `share` nello stato
IMG = bytes(range(256)) * 40
atlas = ws / "personal" / "atlas-shop"
sid = CMDS[14]["arg"]
http("PUT", f"/share/{sid}.json", C.encrypt({"mime": "image/jpeg", "data": base64.b64encode(IMG).decode()}, k))
n_calls = len(cm_calls())
res = send_cmd(CMDS[14])
calls = [c for c in cm_calls()[n_calls:] if c.startswith("report ")]
T.check("R15 (1.19) report with an image → `report <session folder> <temp .jpg> <text> --session <tmux>`, the bytes intact, /result = the fixture's text",
        res and res["ok"] is True and res["text"] == RES[14]["text"] and len(calls) == 1 and calls[0].startswith(f"report {atlas} ") and ".jpg the client says" in calls[0]
        and calls[0].endswith("--session atlas-shop") and (tmp / "report-img").read_bytes() == IMG, str(res) + str(calls))
T.check("R15 (1.19) after the command /share/<id> is deleted and the temporary file is gone",
        sid not in (STORE.get("share") or {}) and not any((rdir2 / "share-tmp").glob("*")), str(list((STORE.get("share") or {}))) + str(list((rdir2 / "share-tmp").glob("*"))))
res = send_cmd(CMDS[15])
T.check("R15 (1.19) text only → `report <folder> - <text> --session field-notes`, «sent to field-notes»",
        res and res["ok"] is True and res["text"] == RES[15]["text"] and f"report {ws / 'personal' / 'field-notes'} - add the Tuesday meeting notes to the draft --session field-notes" in cm_calls(), str(res) + str(cm_calls()[-2:]))
n_calls = len(cm_calls())
res = send_cmd(CMDS[16])
bad = "6f1c2d3e-0125-4000-8000-00000000b001"
http("PUT", f"/share/{bad}.json", C.encrypt({"mime": "text/plain", "data": "aGk="}, k))
res2 = send_cmd(dict(CMDS[14], id="6f1c2d3e-0125-4000-8000-000000000201", arg=bad))
res3 = send_cmd(dict(CMDS[14], id="6f1c2d3e-0125-4000-8000-000000000202", arg="0000-missing"))
big = "6f1c2d3e-0125-4000-8000-00000000b002"
http("PUT", f"/share/{big}.json", {"v": 1, "enc": "A" * 1500001})
res4 = send_cmd(dict(CMDS[14], id="6f1c2d3e-0125-4000-8000-000000000203", arg=big))
res5 = send_cmd(dict(CMDS[15], id="6f1c2d3e-0126-4000-8000-000000000201", text="  "))
T.check("R15 (1.19) refusals in plain words, no report run: a gone session (as in the fixture), a wrong mime, a missing node, a blob over 1.5 MB, nothing to send; the bad nodes deleted",
        res and res["ok"] is False and res["text"] == RES[16]["text"] and res2 and res2["text"] == "image missing or unreadable" and res3 and res3["text"] == "image missing or unreadable"
        and res4 and res4["text"] == "image too large" and res5 and res5["text"] == "empty report: nothing to send"
        and not any(c.startswith("report ") for c in cm_calls()[n_calls:]) and bad not in (STORE.get("share") or {}) and big not in (STORE.get("share") or {}), str([res, res2, res3, res4, res5]))
old, fresh = "6f1c2d3e-0125-4000-8000-00000000b003", "6f1c2d3e-0125-4000-8000-00000000b004"
http("PUT", f"/share/{old}.json", {"v": 1, "enc": "x"}); http("PUT", f"/share/{fresh}.json", {"v": 1, "enc": "x"})
(rdir2 / "share-seen.json").write_text(json.dumps({old: time.time() - 700}))
relay("push")
T.check("R15 (1.19) push prunes a /share node first seen more than 10 minutes ago, keeps a fresh one (remembered in share-seen.json)",
        old not in (STORE.get("share") or {}) and fresh in (STORE.get("share") or {}) and fresh in json.loads((rdir2 / "share-seen.json").read_text()), str(list(STORE.get("share") or {})))
T.check("R15 (1.19) /state carries share {max_bytes: 1500000}, also when there is nothing else (its presence turns «Share» on in the app)",
        json.loads(relay("push", "--dry-run").stdout).get("share") == {"max_bytes": 1500000} and S.build_state({}, 1)["share"] == {"max_bytes": 1500000} and F1["share"] == {"max_bytes": 1500000}, "")
# R17 (30/09, dal telefono): «x» chiusa e «work-x» viva nella stessa cartella hanno lo stesso nome corto — vince la
# viva, la chiusa non entra; e un prompt a una sessione non viva non e' «delivered»
good_bak = good_json.read_text()
good_json.write_text(json.dumps({"sessioni": [GOOD_ORBIT, {"nome": "work-atlas-shop", "cartella": str(atlas), "account": "work", "visto": "2026-09-30T17:30:33"}]}))
relay("push")
names17 = json.loads((rdir2 / "last-state.json").read_text())["names"]
ses17 = [(x["name"], x["state"]) for x in json.loads(relay("push", "--dry-run").stdout)["sessions"] if x["name"] == "atlas-shop"]
good_json.write_text(good_bak)
T.check("R17 a closed session with the same short name as a live one (other account, same folder) stays out: one atlas-shop, live (not gone), and its name maps to the live tmux",
        names17.get("atlas-shop") == "atlas-shop" and len(ses17) == 1 and ses17[0][1] != "gone", str(names17) + str(ses17))
rows_alive("ledger-api", "atlas-shop")
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[1], id="6f1c2d3e-0002-4000-8000-000000000301", session="field-notes"))
res2 = send_cmd(dict(CMDS[1], id="6f1c2d3e-0002-4000-8000-000000000302", op="resume", session="field-notes", arg=None))
T.check("R17 prompt and resume to a session that is not running → ok false «field-notes is not running: nothing sent», no talk",
        res and res["ok"] is False and res["text"] == "field-notes is not running: nothing sent" and res2 and res2["ok"] is False and res2["text"] == res["text"]
        and not any(c.startswith("talk field-notes") for c in cm_calls()[n_calls:]), str(res) + str(res2))
(tmp / "talk-saved").write_text("x")
res = send_cmd(dict(CMDS[1], id="6f1c2d3e-0002-4000-8000-000000000303"))
(tmp / "talk-saved").unlink()
T.check("R17 talk only saved it in the inbox (the session closed meanwhile) → ok false, the message waits there",
        res and res["ok"] is False and res["text"] == "atlas-shop closed meanwhile: the message waits in its inbox and arrives when it restarts", str(res))
rows_alive("ledger-api", "atlas-shop", "field-notes")
# R18 (contratto 1.21, 30/09): il tasto Stop — op interrupt via `claude-master interrupt`; `ops` nello stato
aw18 = json.loads((rdir2 / "awaiting.json").read_text()) if (rdir2 / "awaiting.json").exists() else {}
(rdir2 / "awaiting.json").write_text(json.dumps(dict(aw18, **{"atlas-shop": int(time.time())})))
n_calls = len(cm_calls())
res = send_cmd(CMDS[17])
res2 = send_cmd(CMDS[18])
T.check("R18 (1.21) interrupt → `interrupt <tmux>`: ok «atlas-shop: stopped» and the session leaves awaiting; nothing running → ok false «field-notes: nothing to stop» (as in the fixture)",
        res and res["ok"] is True and res["text"] == RES[17]["text"] and "interrupt atlas-shop" in cm_calls()[n_calls:]
        and "atlas-shop" not in json.loads((rdir2 / "awaiting.json").read_text()) and res2 and res2["ok"] is False and res2["text"] == RES[18]["text"], str(res) + str(res2))
n_calls = len(cm_calls())
res = send_cmd(dict(CMDS[17], id="6f1c2d3e-0128-4000-8000-000000000301", session="orbit-docs"))
T.check("R18 (1.21) interrupt on a gone session → ok false «orbit-docs is not running», no interrupt run",
        res and res["ok"] is False and res["text"] == "orbit-docs is not running" and not any(c.startswith("interrupt") for c in cm_calls()[n_calls:]), str(res))
T.check("R18 (1.21) /state carries ops = the relay's allow-list, with interrupt (the app shows Stop only when it is there)",
        json.loads(relay("push", "--dry-run").stdout).get("ops") == R_OPS and "interrupt" in R_OPS and F1["ops"] == R_OPS, str(R_OPS))
# R19 (contratto 1.22, 30/09): la chat vera per il telefono — op transcript dal jsonl della sessione, a pagine
import re as _re19
tdir19 = home / ".claude" / "projects" / _re19.sub(r"[^A-Za-z0-9]", "-", str((ws / "personal" / "field-notes").resolve()))
tdir19.mkdir(parents=True, exist_ok=True)
(tdir19 / "S-F.jsonl").write_bytes((FIX / "transcript-sample.jsonl").read_bytes())
res = send_cmd(CMDS[19])
res2 = send_cmd(CMDS[20])
T.check("R19 (1.22) transcript «20» → the fixture's JSON: user prompts only (no reminders, notifications, sidechain), assistant texts, one tool entry per call with note and error, the closed turn with start, end and tokens",
        res and res["ok"] is True and res["text"] == RES[19]["text"], (res or {}).get("text", "")[:300])
T.check("R19 (1.22) transcript «20:after=a4.0» → only what came after that entry (the fixture's second page), more false",
        res2 and res2["ok"] is True and res2["text"] == RES[20]["text"] and [e["id"] for e in json.loads(res2["text"])["entries"]] == ["u2.0", "q1.0", "a6.0", "a6.1", "a6.2", "a7.0", "a7.1", "p1.0", "q1789207361000", "img1.0", "q1789207365000", "a5.0"], (res2 or {}).get("text", "")[:200])
p19 = json.loads(send_cmd(dict(CMDS[19], id="6f1c2d3e-0130-4000-8000-000000000301", arg="2:before=a4.0"))["text"])
bad = [send_cmd(dict(CMDS[19], id=f"6f1c2d3e-0130-4000-8000-00000000030{i}", arg=a)) for i, a in ((2, "x"), (3, "5:after=nope"))]
none19 = send_cmd(dict(CMDS[19], id="6f1c2d3e-0130-4000-8000-000000000304", session="atlas-shop-none"))
f19 = {e["id"]: e["files"] for e in json.loads(res["text"])["entries"]}
T.check("R19 (1.22) files: a png written (path made absolute from the session's cwd, mime, size null when absent), no file for a .py edit or a failed write, the PDF of SendUserFile, the image archived by `claude-master report`",
        [x["mime"] for x in f19["a6.0"]] == ["image/png"] and f19["a6.0"][0]["path"].endswith("/field-notes/docs/cover.png") and f19["a6.0"][0]["size"] is None
        and f19["a6.1"] is None and f19["a6.2"] is None and [x["mime"] for x in f19["a7.0"]] == ["application/pdf"]
        and f19["a7.1"][0]["path"].endswith("/docs/reports/2026-09-12-checkout-is-grey.jpg") and all(v is None for k, v in f19.items() if not k.startswith(("a6", "a7"))), str(f19))
u19 = [(e["id"], e["origin"], e["text"]) for e in json.loads(res["text"])["entries"] if e["role"] == "user"]
T.check("R19 (1.22) origin: pc for what was typed in the terminal; the relay's prompt delivered through the socket (a peer message, twice) is one user entry, prefix stripped, origin phone; another session's message is left out",
        u19[:4] == [("u1.0", "pc", "Add the Tuesday meeting notes to the draft"), ("u2.0", "pc", "Also fix the typo in the title"), ("q1.0", "pc", "and push when done"), ("p1.0", "phone", "Also add the attendees list")]
        and all(e["origin"] is None for e in json.loads(res["text"])["entries"] if e["role"] != "user"), str(u19))
n_calls = len(cm_calls())
res21 = send_cmd(CMDS[21])
T.check("R19 (1.22) a prompt with device «phone» → talk with the phone's prefix (not the watch's), «delivered» as in the fixture",
        res21 and res21["ok"] is True and res21["text"] == RES[21]["text"] and any(c.startswith("talk atlas-shop Dall'utente via telefono.") and c.endswith("also add the attendees list --no-wait") for c in cm_calls()[n_calls:]), str(res21) + str(cm_calls()[n_calls:]))
q19 = {e["id"]: (e["queued"], e["note"], e["text"]) for e in json.loads(res["text"])["entries"] if e["role"] == "user"}
T.check("R19 (1.22) typed while a turn runs: shown from the enqueue, with the time it was written; when it enters the turn the same entry stops being queued (no duplicate); one still waiting stays queued; a pasted image gives its text and «1 image(s)»; «[Request interrupted …]» and «[Cross-session delivery notice]» are not the person's messages",
        q19.get("q1789207361000") == (False, None, "then tag the release") and q19.get("q1789207365000") == (True, None, "still waiting in the queue")
        and q19.get("img1.0") == (False, "1 image(s)", "this is how the header looks now") and not any(t.startswith("[") for _, _, t in q19.values())
        and sum(1 for _, _, t in q19.values() if t == "then tag the release") == 1, str(q19))
T.check("R19 (1.22) «2:before=a4.0» → the two entries before it, more true; a bad arg, an unknown id and a session that is not running → ok false in plain words",
        [e["id"] for e in p19["entries"]] == ["a1.1", "a3.0"] and p19["more"] is True and bad[0]["ok"] is False and "expected n" in bad[0]["text"]
        and bad[1]["text"] == "no entry nope in the transcript" and none19["ok"] is False and none19["text"] == "atlas-shop-none is not running", str(p19) + str(bad) + str(none19))
# R22 (contratto 1.24, 02/10): op file — il telefono apre un file che compare nei `files` della trascrizione
import base64 as _b64, os as _os22
_fx = (FIX / "transcript-sample.jsonl").read_text().replace("/home/demo/workspaces", str(ws.resolve()))
(tdir19 / "S-F.jsonl").write_text(_fx)
fn22 = (ws / "personal" / "field-notes").resolve()
(fn22 / "docs").mkdir(parents=True, exist_ok=True)
png22 = _b64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
(fn22 / "docs" / "cover.png").write_bytes(png22)
fcmd = [dict(c, arg=c["arg"].replace("/home/demo/workspaces", str(ws.resolve()))) for c in CMDS[22:24]]
r22 = send_cmd(fcmd[0])
doc22 = (STORE.get("file") or {}).get(fcmd[0]["id"])
blob22 = C.decrypt(doc22, k) if doc22 else {}
T.check("R22 (1.24) file: a path listed in the session's transcript → /file/<cmd id> as {v, enc}, plain {mime, data base64} byte for byte; /result as in the fixture",
        r22 and r22["ok"] is True and r22["text"] == RES[22]["text"] and set(doc22 or {}) == {"v", "enc"}
        and blob22.get("mime") == "image/png" and _b64.b64decode(blob22.get("data") or "") == png22, str(r22) + str(blob22)[:120])
r22b = send_cmd(fcmd[1])
T.check("R22 (1.24) a path not in the transcript (as in the fixture) → «not in the transcript», nothing written to /file",
        r22b and r22b["ok"] is False and r22b["text"] == RES[23]["text"] and fcmd[1]["id"] not in (STORE.get("file") or {}), str(r22b))
pdf22 = str(fn22 / "docs" / "minutes.pdf")
r22c = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000201", arg=pdf22))
(fn22 / "docs" / "minutes.pdf").write_bytes(_os22.urandom(1_300_000))
r22d = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000202", arg=pdf22))
r22e = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000203", session="atlas-shop-none"))
T.check("R22 (1.24) refusals in plain words: a listed file that is missing, a listed non-image over the cap («too large: <bytes>»), a session that is not running",
        r22c and r22c["text"] == "missing or unreadable" and r22d and r22d["text"] == "too large: 1300000" and r22e and r22e["text"] == "no session atlas-shop-none", str([r22c, r22d, r22e]))
from PIL import Image as _Im22
rep22 = (ws / "personal" / "atlas-shop").resolve() / "docs" / "reports"   # l'immagine archiviata da `claude-master report`
rep22.mkdir(parents=True, exist_ok=True)
_Im22.frombytes("RGB", (1600, 1600), _os22.urandom(1600 * 1600 * 3)).save(rep22 / "2026-09-12-checkout-is-grey.jpg", "JPEG", quality=95)
jpg22 = str(rep22 / "2026-09-12-checkout-is-grey.jpg")
r22f = send_cmd(dict(fcmd[0], id="6f1c2d3e-0140-4000-8000-000000000204", arg=jpg22))
doc22f = (STORE.get("file") or {}).get("6f1c2d3e-0140-4000-8000-000000000204") or {}
blob22f = C.decrypt(doc22f, k) if doc22f else {}
T.check("R22 (1.24) a listed image over the cap is reduced to a JPEG that fits (enc ≤ 1 500 000)",
        _os22.path.getsize(jpg22) > 1_200_000 and r22f and r22f["ok"] is True and blob22f.get("mime") == "image/jpeg"
        and len(doc22f.get("enc") or "") <= 1_500_000 and _b64.b64decode(blob22f.get("data") or "")[:2] == b"\xff\xd8", f"{_os22.path.getsize(jpg22)} {r22f} {len(doc22f.get('enc') or '')}")
T.check("R22 (1.24) /state ops carries file", "file" in json.loads(relay("push", "--dry-run").stdout).get("ops", []), "")
old22, fresh22 = "6f1c2d3e-0140-4000-8000-00000000f001", "6f1c2d3e-0140-4000-8000-00000000f002"
http("PUT", f"/file/{old22}.json", {"v": 1, "enc": "x"}); http("PUT", f"/file/{fresh22}.json", {"v": 1, "enc": "x"})
(rdir2 / "file-seen.json").write_text(json.dumps({old22: time.time() - 700}))
relay("push")
T.check("R22 (1.24) push prunes a /file node not read for more than 10 minutes, keeps a fresh one",
        old22 not in (STORE.get("file") or {}) and fresh22 in (STORE.get("file") or {}), str(list(STORE.get("file") or {})))
# R23 (contratto 1.25, 02/10): op slash — un comando slash digitato nel pannello della sessione, solo dalla lista
rows_alive("ledger-api", "atlas-shop", "field-notes")
n_calls = len(cm_calls())
r23 = send_cmd(CMDS[24])
r23b = send_cmd(CMDS[25])
T.check("R23 (1.25) slash compact with its text → typed in the pane: `talk field-notes /compact <text> --via tmux --no-wait`, no prefix; /result as in the fixture",
        r23 and r23["ok"] is True and r23["text"] == RES[24]["text"]
        and "talk field-notes /compact keep the meeting notes and the open questions --via tmux --no-wait" in cm_calls()[n_calls:], str(r23) + str(cm_calls()[n_calls:]))
T.check("R23 (1.25) a command not in relay.slash_commands (as in the fixture) → «not allowed: model», nothing typed",
        r23b and r23b["ok"] is False and r23b["text"] == RES[25]["text"] and not any(c.startswith("talk field-notes /model") for c in cm_calls()[n_calls:]), str(r23b))
n_calls = len(cm_calls())
r23c = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000201", session="atlas-shop"))
r23d = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000202", session="ledger-api"))
r23e = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000203", session="nessuna"))
r23f = send_cmd(dict(CMDS[24], id="6f1c2d3e-0150-4000-8000-000000000204", arg="/exit", text=None))
T.check("R23 (1.25) a busy session and one on a dialog → «<name> is busy», a missing one → «no session <name>», nothing typed; /exit (with its slash) is typed like the others",
        r23c and r23c["text"] == "atlas-shop is busy" and r23d and r23d["text"] == "ledger-api is busy" and r23e and r23e["text"] == "no session nessuna"
        and r23f and r23f["ok"] is True and r23f["text"] == "sent /exit to field-notes"
        and [c for c in cm_calls()[n_calls:] if c.startswith("talk ")] == ["talk field-notes /exit --via tmux --no-wait"], str([r23c, r23d, r23e, r23f]) + str(cm_calls()[n_calls:]))
st23 = json.loads(relay("push", "--dry-run").stdout)
T.check("R23 (1.25) /state carries slash = relay.slash_commands without «/», and ops carries slash",
        st23.get("slash") == ["compact", "clear", "exit", "context", "cost"] and "slash" in st23.get("ops", []) and F1["slash"] == st23["slash"], str(st23.get("slash")))
T.check("R14 (1.18) events-sample: a recap, a night_report and the quota resume, with the shape of the other events",
        [e["kind"] for e in EV[-3:]] == ["recap", "night_report", "quota"] and all(set(e) == set(EV[0]) for e in EV) and EV[-3]["ref"] == "2026-09-12", str(EV[-3:])[:300])
rows_alive("ledger-api", "atlas-shop", "field-notes")
r = relay("push", "--dry-run"); dry11 = json.loads(r.stdout)
p_atlas = next(p_ for p_ in dry11["projects"] if p_["name"] == "atlas-shop")
newest = int(max(f.stat().st_mtime for f in tdir_a.iterdir() if f.suffix == ".jsonl"))
T.check("R11 (1.13) projects[].last_used = the newest transcript of that folder (epoch s); null where there is none",
        p_atlas.get("last_used") == newest and all("last_used" in p_ for p_ in dry11["projects"]) and any(p_["last_used"] is None for p_ in dry11["projects"]), str(dry11["projects"]))
ans = dict(CMDS[0], id="6f1c2d3e-0011-4000-8000-000000000101", arg="text:ship it tonight")
res = send_cmd(ans)
T.check("R6 answer text:<text> → `answer work-ledger-api --text <text>`, «answered 3. ship it tonight»", res and res["ok"] is True and res["text"] == "answered 3. ship it tonight" and "answer work-ledger-api --text ship it tonight" in cm_calls(), str(res) + str(cm_calls()[-2:]))
res = send_cmd(dict(ans, id="6f1c2d3e-0011-4000-8000-000000000102", arg="chat"))
T.check("R6 answer chat → `answer work-ledger-api --chat`, «answered 4. Chat about this»", res and res["ok"] is True and res["text"] == "answered 4. Chat about this" and "answer work-ledger-api --chat" in cm_calls(), str(res))
n_a = len([c for c in cm_calls() if c.startswith("answer work-ledger-api") and "--show" not in c])
res = send_cmd(dict(ans, id="6f1c2d3e-0011-4000-8000-000000000103", arg="text:  "))
res2 = send_cmd(dict(ans, id="6f1c2d3e-0011-4000-8000-000000000104", arg="maybe"))
T.check("R6 answer «text:» empty → ok false «empty text»; an arg that is neither a number, text: nor chat → ok false, nothing run, the daemon stays up", res and res["ok"] is False and res["text"] == "empty text" and res2 and res2["ok"] is False and res2["text"] == "answer maybe: expected a number, text:<text> or chat" and len([c for c in cm_calls() if c.startswith("answer work-ledger-api") and "--show" not in c]) == n_a and serve_pid() == pid1, str(res) + str(res2))
n_ans = len([c for c in cm_calls() if c == "answer work-ledger-api 1"])
http("PUT", f"/cmd/{CMDS[0]['id']}.json", C.encrypt(CMDS[0], k))
T.wait_until(lambda: CMDS[0]["id"] not in (STORE.get("cmd") or {}), 8)
time.sleep(1)
T.check("R6 the same id again → ignored (no second `answer`), /cmd cleaned", len([c for c in cm_calls() if c == "answer work-ledger-api 1"]) == n_ans and CMDS[0]["id"] not in (STORE.get("cmd") or {}), str(cm_calls()[-3:]))
res = send_cmd({"id": "6f1c2d3e-0009-4000-8000-000000000009", "op": "mode", "session": "atlas-shop", "arg": "plan", "issued": 1, "by": "watch"})
T.check("R6 an op outside the allow-list → ok false «op mode not allowed», nothing run", res and res["ok"] is False and res["text"] == "op mode not allowed", str(res))
http("PUT", "/cmd/garbage.json", {"v": 1, "enc": "bm90aGluZw=="})
T.wait_until(lambda: "garbage" not in (STORE.get("cmd") or {}), 5)
T.check("R6 an undecipherable command is discarded (deleted, no result)", "garbage" not in (STORE.get("cmd") or {}) and "garbage" not in (STORE.get("result") or {}), str(STORE.get("cmd")))
# rete giu': il daemon riconnette e poi serve ancora
fail = tmp / "rtdb-fail"; fail.write_text("x")
os.environ["FAKE_RTDB_FAIL"] = str(fail)
time.sleep(1)
r = relay("status")
fail.unlink()
T.wait_until(lambda: "riconness" in (rdir2 / "relay.log").read_text(), 8)
T.check("R6 RTDB down (503) → the daemon logs the reconnection with backoff and stays alive", "riconnessione" in (rdir2 / "relay.log").read_text() and serve_pid() == pid1, (rdir2 / "relay.log").read_text()[-400:])
os.environ.pop("FAKE_RTDB_FAIL", None)
T.wait_until(lambda: "riconnesso" in (rdir2 / "relay.log").read_text(), 12)
res = send_cmd(dict(CMDS[3], id="6f1c2d3e-0004-4000-8000-0000000000cc"), wait=12)
T.check("R6 after the network is back a command is served again", res and res["ok"], str(res) + (rdir2 / "relay.log").read_text()[-300:])
r = relay("status")
T.check("R6 status: enabled, firebase url, key ok, service account ok, serve alive with the pid, last push time", r.returncode == 0 and "abilitato" in r.stdout and URL in r.stdout and f"VIVO pid {pid1}" in r.stdout and "chiave:           ok" in r.stdout and "service account:  ok" in r.stdout, r.stdout + r.stderr)
r = relay("install")
T.check("R6 install: two cron lines (relay ensure, relay push --async every minute)", r.returncode == 0 and "relay ensure" in cron.read_text() and "relay push --async" in cron.read_text() and cron.read_text().count("* * * * *") == 2, cron.read_text() + r.stdout)
r = relay("off")
T.check("R6 off: the daemon stops, the cron stays", r.returncode == 0 and T.wait_until(lambda: serve_pid() == 0, 4) and "relay ensure" in cron.read_text(), r.stdout + r.stderr)
relay("ensure")
r = relay("uninstall")
T.check("R6 uninstall: daemon stopped and cron lines removed", r.returncode == 0 and T.wait_until(lambda: serve_pid() == 0, 4) and "claude-master relay" not in cron.read_text(), r.stdout + cron.read_text())
# 0.4.20: senza crontab (o cryptography) install e pair si fermano prima di scrivere o chiedere: esce 5, comando da lanciare
ENV_NOCRON = dict(ENV, CM_CRONTAB_CMD=str(tmp / "no-such-crontab"))
before = cron.read_text()
r = relay("install", env=ENV_NOCRON)
T.check("R6 install without crontab → exit 5, message with `apt install cron`, crontab untouched", r.returncode == 5 and "apt install cron" in r.stderr and cron.read_text() == before, f"rc={r.returncode} " + r.stdout + r.stderr)
r = relay("pair", "--timeout", "2", env=ENV_NOCRON)
T.check("R6 pair without crontab → exit 5 before printing a code, /pair untouched", r.returncode == 5 and "apt install cron" in r.stderr and not re.search(r"\b\d{6}\b", r.stdout), f"rc={r.returncode} " + r.stdout + r.stderr)

# R7: hook e bot chiamano push --async
def hook(ev, payload, env=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True, env=env or ENV, timeout=30)


def state_puts():
    return len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])


n0 = state_puts()
r = hook("PermissionRequest", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "tool_name": "Bash", "tool_input": {"command": "rm -rf build", "description": "clean"}})
wf = json.loads((state_dir / "waiting" / "S-A").read_text())
T.check("R7 PermissionRequest: waiting/<sid> is JSON {tool, input{command, description}}; a push follows (async) → a new PUT of /state", r.returncode == 0 and wf["tool"] == "Bash" and wf["input"]["command"] == "rm -rf build" and T.wait_until(lambda: state_puts() > n0, 15), r.stdout + r.stderr + str(wf))
r = relay("push", "--dry-run")
dry7 = json.loads(r.stdout)
a7 = next(s_ for s_ in dry7["sessions"] if s_["name"] == "atlas-shop")
T.check("R7 …and /state now shows atlas-shop waiting on a permission with tier high (rm -rf) and the question text from the screen", a7["state"] == "waiting" and a7["question"]["kind"] == "permission" and a7["question"]["tier"] == "high", str(a7["question"]))
(state_dir / "waiting" / "S-A").unlink()
r = hook("PermissionRequest", {"session_id": "S-F", "cwd": str(ws / "personal" / "field-notes"), "tool_name": "AskUserQuestion",
                               "tool_input": {"questions": [{"question": "Procedo con il deploy di prova?", "header": "Deploy", "options": [{"label": "Sì", "description": "vai"}, {"label": "No", "description": "aspetta"}]}]}})
wf2 = json.loads((state_dir / "waiting" / "S-F").read_text())
r = relay("push", "--dry-run"); dry7b = json.loads(r.stdout)
f7 = next(s_ for s_ in dry7b["sessions"] if s_["name"] == "field-notes")
T.check("R7b AskUserQuestion before the dialog is on screen (answer --show finds nothing): waiting/<sid> keeps question + option labels from the payload, and /state carries text and options 1-2 with kind ask", wf2["input"]["question"] == "Procedo con il deploy di prova?" and wf2["input"]["options"] == ["Sì", "No"] and f7["state"] == "waiting" and f7["question"]["text"] == "Procedo con il deploy di prova?" and [o["label"] for o in f7["question"]["options"]] == ["Sì", "No"] and f7["question"]["kind"] == "ask", str(wf2) + str(f7["question"]))
(state_dir / "waiting" / "S-F").unlink()
n0 = state_puts()
r = hook("UserPromptSubmit", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "prompt": "vai"})
T.check("R7 UserPromptSubmit: a «prompt» row in the ledger (turn_started), no push", r.returncode == 0 and any(json.loads(l).get("event") == "prompt" and json.loads(l).get("session_id") == "S-A" for l in ledger.read_text().splitlines()) and state_puts() == n0, ledger.read_text()[-200:])
n0 = state_puts()
r = hook("Stop", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "last_assistant_message": "Fatto.\nEsito: seed rivisti.\nWatch: Seed rivisti"})
T.check("R7 Stop: ledger row with watch, then a push", r.returncode == 0 and T.wait_until(lambda: state_puts() > n0, 15) and any(json.loads(l).get("watch") == "Watch: Seed rivisti" for l in ledger.read_text().splitlines()), r.stdout + r.stderr)
n0 = state_puts()
hook("SessionEnd", {"session_id": "S-F", "cwd": str(ws / "personal" / "field-notes"), "reason": "other"})
T.check("R7 SessionEnd → push", T.wait_until(lambda: state_puts() > n0, 15), "")
# PostToolUse (14/09, dall'app): la domanda risposta da tastiera o telefono deve sparire anche dall'orologio in
# background, che si sveglia solo con FCM, cioe' con un evento: il push dopo l'hook ha lo stato senza domanda → answered
# il finto bus scrive STORE da un altro thread mentre il test lo scorre: si scorre una copia (22-23/09: la release
# si e' fermata due volte su «dictionary changed size during iteration»)
def answered_count(name):
    return sum(1 for v in list((STORE.get("events") or {}).values()) if (lambda e: e.get("kind") == "answered" and e.get("session") == name)(C.decrypt(v, k) or {}))


(state_dir / "waiting" / "S-A").write_text(json.dumps({"tool": "Bash", "input": {"command": "make test"}}))
relay("push")
a0, n0 = answered_count("atlas-shop"), state_puts()
r = hook("PostToolUse", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "tool_name": "Bash"})
T.check("R7 PostToolUse with the waiting flag → flag removed, a push, and an «answered» event for the session (FCM wakes the watch)",
        r.returncode == 0 and not (state_dir / "waiting" / "S-A").exists() and T.wait_until(lambda: answered_count("atlas-shop") > a0, 15) and state_puts() > n0, r.stdout + r.stderr)
def settle(rounds=24):
    """Aspetta che il conto delle PUT resti fermo: le push asincrone gia' partite vanno esaurite prima di
    misurare, altrimenti la loro scrittura sembra quella dell'hook."""
    prev = -1
    for _ in range(rounds):
        cur = state_puts()
        if cur == prev:
            return
        prev = cur
        time.sleep(1.5)


settle()
write_cfg(enabled=False)
settle(8)   # e anche dopo lo spegnimento: un figlio partito un attimo prima ha gia' letto la config
n0 = state_puts()
r = hook("Stop", {"session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "last_assistant_message": "x"})
time.sleep(2.5)
T.check("R7 relay.enabled=false: the hook does not push", r.returncode == 0 and state_puts() == n0, "")
write_cfg()
os.environ.update({"CLAUDE_MASTER_CONFIG": str(cfg), "HOME": str(home), "CM_RELAY_CM": str(fake_cm)})
n0 = state_puts()
(rdir2 / "awaiting.json").write_text(json.dumps({"atlas-shop": int(time.time())}))
r = relay("push", "--dry-run"); dryaw = json.loads(r.stdout)
a_aw = next(s_ for s_ in dryaw["sessions"] if s_["name"] == "atlas-shop")
T.check("R7b (1.2) a session awaiting a wrist prompt is «awaiting» and its tool is read too (before: only busy ones, so the card had no activity)", a_aw["state"] == "awaiting" and "tool" in a_aw, str((a_aw["state"], a_aw["tool"])))
# la voce scade quando il turno finisce (uno stop dopo il prompt) o dopo awaiting_max_s: senza questo la
# sessione restava «awaiting» per sempre, col turno e il tool di ore prima (dal vivo 14/09 08:22)
sent = time.time() - 60
(rdir2 / "awaiting.json").write_text(json.dumps({"atlas-shop": sent}))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": iso(time.time()), "event": "stop", "session_id": "S-A", "cwd": str(ws / "personal" / "atlas-shop"), "account": "personal", "pid": 8, "last": "fine turno", "tail": "fine turno", "esito": ""}) + "\n")
r = relay("push", "--dry-run"); dry_done = json.loads(r.stdout)
a_done = next(s_ for s_ in dry_done["sessions"] if s_["name"] == "atlas-shop")
T.check("R7c the awaiting entry expires when the turn ends (a stop after the prompt): the session is no longer «awaiting» and awaiting.json is cleaned", a_done["state"] != "awaiting" and json.loads((rdir2 / "awaiting.json").read_text()) == {}, str(a_done["state"]) + str((rdir2 / "awaiting.json").read_text()))
(rdir2 / "awaiting.json").write_text(json.dumps({"atlas-shop": time.time() - 3600}))
r = relay("push", "--dry-run"); dry_old = json.loads(r.stdout)
a_old = next(s_ for s_ in dry_old["sessions"] if s_["name"] == "atlas-shop")
T.check("R7c an entry older than relay.awaiting_max_s expires as well", a_old["state"] != "awaiting" and json.loads((rdir2 / "awaiting.json").read_text()) == {}, str(a_old["state"]))
(rdir2 / "awaiting.json").write_text("{}")

# R8 (16/09, passo 3 del ritiro di Telegram): il bus non prende → il polso non riceve, e dopo la soglia l'avviso
# passa da Telegram; quando torna a funzionare la scorta si spegne
write_cfg(telegram_fallback_after_s=1)
(rdir2 / "last-state.json").unlink(missing_ok=True)   # senza stato precedente ogni sessione e' un evento «launched»
(rdir2 / "fallback.json").unlink(missing_ok=True)
TG_CALLS["sendMessage"].clear()
failr = tmp / "rtdb-fail-2"; failr.write_text("x")
os.environ["FAKE_RTDB_FAIL"] = str(failr)   # il finto RTDB gira in QUESTO processo: l'ambiente e' il suo
r = relay("push")
down = json.loads((rdir2 / "fallback.json").read_text()) if (rdir2 / "fallback.json").is_file() else {}
T.check("R8 the bus refuses → fallback.json records since when the watch is not receiving, and the push fails loudly", r.returncode != 0 and float(down.get("since") or 0) > 0, r.stdout + r.stderr + str(down))
time.sleep(1.2)
r = relay("push")
T.check("R8 past relay.telegram_fallback_after_s the notice goes to Telegram, with the minutes and the events", any("ledger-api" in c.get("text", "") for c in TG_CALLS["sendMessage"]), str(TG_CALLS["sendMessage"])[:400])
failr.unlink(); os.environ.pop("FAKE_RTDB_FAIL", None)
r = relay("push")
T.check("R8 the bus works again → fallback.json is gone (the fallback switches off)", r.returncode == 0 and not (rdir2 / "fallback.json").exists(), r.stdout + r.stderr)

# R16 (contratto 1.20, 29/09): il bus funziona ma nessun dispositivo legge → dopo la soglia Telegram. Letto = /seen/<uid>
# scritto dopo l'evento (ora del server, in ms); solo gli uid accoppiati; senza nessun /seen resta il ripiego R8
def unseen_run(seen):
    STORE["seen"] = seen
    (rdir2 / "last-state.json").unlink(missing_ok=True)   # ogni sessione torna «launched»: eventi nuovi
    TG_CALLS["sendMessage"].clear()
    r1 = relay("push")
    time.sleep(1.2)
    r2 = relay("push")
    return r1, r2, [c.get("text", "") for c in TG_CALLS["sendMessage"]]


devs16 = list(json.loads((rdir2 / "devices.json").read_text()))
STORE.pop("seen", None)
r1, r2, tg = unseen_run({})
T.check("R16 (1.20) no device writes /seen (an app that does not know it) → no Telegram, no pending.json: only the R8 fallback applies",
        r1.returncode == 0 and r2.returncode == 0 and tg == [] and not (rdir2 / "pending.json").exists(), str(tg) + r1.stderr[-200:])
r1, r2, tg = unseen_run({devs16[0]: int(time.time() * 1000) + 5000})
T.check("R16 (1.20) a paired device read after the events (server time in ms) → seen, no Telegram, pending.json empty",
        tg == [] and json.loads((rdir2 / "pending.json").read_text()) == [], str(tg))
r1, r2, tg = unseen_run({devs16[0]: int((time.time() - 3600) * 1000), "u-revoked": int(time.time() * 1000)})
T.check("R16 (1.20) no paired device read since the events (an unpaired uid does not count) → past the threshold one Telegram notice with the events, pending emptied",
        len(tg) == 1 and tg[0].startswith("Nessun dispositivo legge gli avvisi da 60 minuti") and "ledger-api" in tg[0] and json.loads((rdir2 / "pending.json").read_text()) == [], str(tg)[:400])
TG_CALLS["sendMessage"].clear()
relay("push")
T.check("R16 (1.20) the same events are not sent twice", TG_CALLS["sendMessage"] == [], str(TG_CALLS["sendMessage"])[:200])
STORE.pop("seen", None)
(rdir2 / "pending.json").unlink(missing_ok=True)


# R9 (contratto 1.11, 16/09): ogni sessione porta modello, effort e contesto, letti dalla sua trascrizione;
# senza trascrizione i tre campi ci sono e sono null — mai stimati
import re as _re9
tdir9 = home / ".claude" / "projects" / _re9.sub(r"[^A-Za-z0-9]", "-", str((ws / "personal" / "atlas-shop").resolve()))
tdir9.mkdir(parents=True, exist_ok=True)
with open(tdir9 / "S-A.jsonl", "w") as f9:
    f9.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-opus-5[1m]", "marketingName": "Opus 5 (1M context)"}}}) + "\n")
    f9.write(json.dumps({"type": "assistant", "effort": "high", "message": {"model": "claude-opus-5", "usage": {"input_tokens": 0, "cache_read_input_tokens": 250_000, "cache_creation_input_tokens": 0}}}) + "\n")
r = relay("push", "--dry-run"); dry9 = json.loads(r.stdout)
a9 = next(s_ for s_ in dry9["sessions"] if s_["name"] == "atlas-shop")
l9 = next(s_ for s_ in dry9["sessions"] if s_["name"] == "ledger-api")
T.check("R9 (1.11) the session with a transcript carries model {id,label}, effort and context (250k of 1M → 25 %)",
        a9["model"] == {"id": "claude-opus-5[1m]", "label": "Opus 5"} and a9["effort"] == "high" and a9["context"] == 25, str({k: a9.get(k) for k in ("model", "effort", "context")}))
T.check("R9 (1.11) a session without a readable transcript has the three fields as null, not missing and not guessed",
        ("model" in l9 and "effort" in l9 and "context" in l9) and l9["model"] is None and l9["effort"] is None and l9["context"] is None, str({k: l9.get(k) for k in ("model", "effort", "context")}))

# R21 (30/09, dal telefono: prompt e risposte non nell'ordine dato): i comandi che arrivano insieme (il put iniziale dopo
# una riconnessione) si eseguono in ordine di `issued`, non di chiave (uuid casuali)
_env21 = dict(os.environ)
os.environ.update({"CLAUDE_MASTER_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
RL = load("cm-relay")
os.environ.clear(); os.environ.update(_env21)
cmds21 = {"z-first-key": C.encrypt({"op": "prompt", "issued": 1789210903}, k), "a-last-key": C.encrypt({"op": "answer", "issued": 1789210905}, k),
          "m-mid": C.encrypt({"op": "prompt", "issued": 1789210904}, k), "b-plain": {"op": "screen", "issued": 1789210901}}
T.check("R21 commands that arrive together run in the order they were issued, not in key order — the person's commands first, the passive reads (screen) after",
        [c for c, _ in RL.in_order(cmds21)] == ["z-first-key", "m-mid", "a-last-key", "b-plain"], str([c for c, _ in RL.in_order(cmds21)]))
cmds21b = {"t-old": C.encrypt({"op": "transcript", "session": "atlas-shop", "arg": "20", "issued": 1789210910}, k),
           "t-new": C.encrypt({"op": "transcript", "session": "atlas-shop", "arg": "20:after=x", "issued": 1789210914}, k),
           "t-other": C.encrypt({"op": "transcript", "session": "field-notes", "arg": "20", "issued": 1789210911}, k),
           "p-1": C.encrypt({"op": "prompt", "session": "atlas-shop", "arg": "go", "issued": 1789210912}, k)}
_env21 = dict(os.environ); os.environ.update({"CLAUDE_MASTER_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
got21 = [c for c, _ in RL.in_order(cmds21b)]
os.environ.clear(); os.environ.update(_env21)
sup = (STORE.get("result") or {}).get("t-old")
T.check("R21 several reads of the same session waiting together → only the newest runs; the older one gets «superseded» at once; the prompt goes first",
        got21 == ["p-1", "t-other", "t-new"] and sup and C.decrypt(sup, k)["ok"] is False and "superseded" in C.decrypt(sup, k)["text"], str(got21) + str(sup)[:80])

T.finish()

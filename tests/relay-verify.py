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
     ri-pair revoca l'uid vecchio
R6   relay serve: SSE su /cmd, i sette op del contratto eseguiti via dispatcher finto → /result, /cmd cancellato,
     /state ripubblicato; duplicati ignorati; op fuori allow-list rifiutato; launch fuori da projects rifiutato;
     RTDB giù → riconnessione; status/ensure/install/uninstall/off
R7   cm-hook.py: waiting/<sid> in JSON con tool_input; PermissionRequest/Stop/SessionStart/SessionEnd → push
     --async (relay abilitata); disabilitata → niente; bot follow/unfollow → push
"""
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

# R0: fixture identiche al contratto
for f in ("state-1-question", "state-2-idle", "state-3-stale", "events-sample", "cmd-result-sample"):
    a = FIX / f"{f}.json"; b = WATCH / "contract" / f"{f}.json"
    T.check(f"R0 fixture {f} identical to the app contract (skipped if the app repo is absent)", a.is_file() and ((not b.is_file()) or a.read_bytes() == b.read_bytes()), str(b))

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
F1 = json.loads((FIX / "state-1-question.json").read_text())
F2 = json.loads((FIX / "state-2-idle.json").read_text())
F3 = json.loads((FIX / "state-3-stale.json").read_text())
SRC1 = {
    "host": "crostini-demo", "root": ROOT_WS, "prefixes": ["work-"], "high_words": None,
    "rows": [
        {"name": "work-ledger-api", "tmux": "work-ledger-api", "account": "work", "cwd": ROOT_WS + "/work/clients/ledger-api", "status": "waiting", "waiting": True, "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "link": "https://claude.ai/code/session_01CnGG8im7UG4KtjbPDx9fst", "attached": False, "started_at": 1789210000000},
        {"name": "atlas-shop", "tmux": "atlas-shop", "account": "personal", "cwd": ROOT_WS + "/personal/atlas-shop", "status": "busy", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": True, "started_at": 1789209000000},
        {"name": "field-notes", "tmux": "field-notes", "account": "personal", "cwd": ROOT_WS + "/personal/field-notes", "status": "idle", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "link": "https://claude.ai/code/session_03fieldnotes", "attached": False, "started_at": 1789120000000},
        {"name": "work-orbit-docs", "tmux": "work-orbit-docs", "account": "work", "cwd": ROOT_WS + "/work/own/orbit-docs", "status": "dead", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000004", "link": "", "attached": False, "visto_ts": 1789200000},
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
    "night": {"queued": 2, "running": None},
    "recap": {"date": "2026-09-12", "items": [{"project": "atlas-shop", "done": "Migrations 008-011 applied, tests green", "next": "Review the seeds and the admin page"}, {"project": "ledger-api", "done": "Deploy ready", "next": "Wait for the go"}]},
    "follow": {"work-ledger-api"}, "awaiting": set(),
    "next": {"work-ledger-api": "Wait for the go", "atlas-shop": "Review the seeds and the admin page", "work-orbit-docs": "Pick up the pricing page"},
    "tools": {"atlas-shop": "Bash pytest -q tests"},
    "tool_notes": {"atlas-shop": "Run the test suite"},
    "icons": {"work-ledger-api": "🟦", "atlas-shop": "🟢", "field-notes": "🟡", "work-orbit-docs": "🟪"},
    "next_at": {"work-ledger-api": 1789171200, "atlas-shop": 1789171200, "work-orbit-docs": 1789171200}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]},
    # 1.11: quello che ogni sessione viva sta usando (la gone non ha trascrizione da rileggere)
    "runtime": {"work-ledger-api": {"model": {"id": "claude-opus-5[1m]", "label": "Opus 5"}, "effort": "high", "context": 62},
                "atlas-shop": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 18},
                "field-notes": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "low", "context": 4}},
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
T.check("R2 rules: order waiting/busy/idle/gone then alphabetical, short ≤ 200 (1.6), full ≤ 600, n from 1, ≤ 8 KB", [x["state"] for x in st1["sessions"]] == ["waiting", "busy", "idle", "gone"] and all(len(x["outcome"]["short"]) <= 200 and len(x["outcome"]["full"]) <= 600 for x in st1["sessions"] if x["outcome"]) and [o["n"] for o in st1["sessions"][0]["question"]["options"]] == [1, 2] and S.size_of(st1) <= 8192, str(S.size_of(st1)))
SRC2 = {"host": "crostini-demo", "root": ROOT_WS, "prefixes": ["work-"],
        "rows": [{"name": "atlas-shop", "tmux": "atlas-shop", "account": "personal", "cwd": ROOT_WS + "/personal/atlas-shop", "status": "idle", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": False, "started_at": 1789214000000}],
        "ledger": [{"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789213900), "last": "x", "esito": "Esito: seeds and admin page reviewed, 42 tests green.", "tail": "Esito: seeds and admin page reviewed, 42 tests green.\nWatch: Seeds and admin page reviewed", "watch": "Watch: Seeds and admin page reviewed"}],
        "questions": {}, "quota": {"personal": {"cinque_ore_pct": 24, "settimana_pct": 38, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": False}, "work": {"cinque_ore_pct": 3, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": False}},
        "projects": [{"path": ROOT_WS + "/personal/atlas-shop", "name": "atlas-shop", "account": "personal", "last_used": 1789213900}], "night": {"queued": 0, "running": None}, "recap": {"date": "2026-09-12", "items": []},
        "follow": set(), "awaiting": set(), "next": {"atlas-shop": "Test deploy on staging"}, "tools": {}, "icons": {"atlas-shop": "🟢"}, "next_at": {"atlas-shop": 1789171200}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]},
        "runtime": {"atlas-shop": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 18}}}
SRC2["account_kinds"] = KINDS
st2 = S.build_state(SRC2, 1789214400)
T.check("R2 build_state(src) == state-2-idle.json", st2 == F2, diff(st2, F2) or "equal")
SRC3 = {"host": "crostini-demo", "root": ROOT_WS, "prefixes": [], "rows": [], "ledger": [], "questions": {},
        "quota": {"personal": {"cinque_ore_pct": 0, "settimana_pct": 36, "reset_settimanale": 1789610400, "reset_cinque_ore": 1789228800, "vecchia": True}, "work": {"cinque_ore_pct": None, "settimana_pct": 75, "reset_settimanale": 1789444800, "reset_cinque_ore": 1789225200, "vecchia": True}},
        "projects": [], "night": {"queued": 0, "running": None}, "recap": {"date": "2026-09-12", "items": []}, "follow": set(), "awaiting": set(), "next": {}, "tools": {}, "choices": {"models": [{"id": "claude-opus-5[1m]", "label": "Opus 5"}, {"id": "claude-fable-5-1", "label": "Fable 5.1"}, {"id": "claude-sonnet-5", "label": "Sonnet 5"}, {"id": "claude-haiku-4-5", "label": "Haiku 4.5"}], "efforts": ["low", "medium", "high", "xhigh", "max"]}}
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
  talk) echo "consegnato" ;;
  model) echo "$2: model Sonnet 5, this session only" ;;
  effort) if [ "$2" = "atlas-shop" ]; then echo "atlas-shop is working: try again when it is idle"; exit 3; else echo "$2: effort $3, this session only"; fi ;;
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
    d = {"language": "it", "state_dir": str(state_dir), "default_account": "personal",
         "workspace": {"root": str(ws), "excluded_dirs": [".git"], "project_dirs": ["personal", "work/clients", "work/own"]},
         "folder_map": [{"path": str(ws / "work"), "account": "work"}, {"path": str(ws / "personal"), "account": "personal"}],
         "bot": {"api_base": TG_API, "token_file": str(tgdir / ".env"), "access_file": str(tgdir / "access.json")},
         "accounts": {"personal": {"config_dir": str(home / ".claude")}, "work": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "work-"}},
         "relay": {"enabled": enabled, "firebase_url": URL, "service_account": str(SA), "token_url": URL + "/token", "fcm_url": URL,
                   "dir": str(rdir2), "host": "crostini-test", "debounce_s": 1, "fcm_topic": "watch", **extra}}
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
T.check("R4 push --dry-run: clear JSON on stdout, no HTTP; sessions ordered ❓ ▶ ✓ ✗ with short names, the question whole with kind ask and options 1-2, the busy session's outcome from the ledger (short = Watch line), gone from the snapshot, quota, projects with accounts from folder_map, recap, night", r.returncode == 0 and len(CALLS["requests"]) == n_req and [(x["name"], x["state"]) for x in dry.get("sessions", [])] == [("ledger-api", "waiting"), ("atlas-shop", "busy"), ("field-notes", "idle"), ("orbit-docs", "gone")] and dry["sessions"][0]["question"]["text"] == "Deploy ready, waiting for the client ok. Deploy now?" and dry["sessions"][0]["question"]["kind"] == "ask" and [o["label"] for o in dry["sessions"][0]["question"]["options"]] == ["yes", "no"] and dry["sessions"][0]["question"]["asked_at"] == 1789210500 and dry["sessions"][0]["followed"] is True and dry["sessions"][0]["project"] == "work/clients/ledger-api" and dry["sessions"][1]["outcome"]["short"] == "Migrazioni applicate, test verdi" and dry["sessions"][1]["turn_started"] == 1789210700 and dry["sessions"][1]["next"] == "Review the seeds and the admin page" and dry["sessions"][3]["since"] == S.epoch("2026-09-12T09:00:00") and dry["quota"]["work"] == {"h5": None, "w7": 75, "reset_w7": 1789444800, "reset_h5": 1789225200, "stale": True, "kind": "work"} and {(p["name"], p["account"]) for p in dry["projects"]} == {("atlas-shop", "personal"), ("field-notes", "personal"), ("ledger-api", "work"), ("orbit-docs", "work")} and dry["host"] == "crostini-test" and dry["night"] == {"queued": 0, "running": None} and dry["v"] == 1, r.stdout[:600] + r.stderr)
T.check("R4 (1.1) every live session carries icon (from cm-color's registry, stable) and color «#RRGGBB»; the gone one has none on the first push", all(x["icon"] and re.match(r"^#[0-9A-F]{6}$", x["color"] or "") for x in dry["sessions"] if x["state"] != "gone") and dry["sessions"][3]["icon"] is None, str([(x["name"], x["icon"], x["color"]) for x in dry["sessions"]]))
r = relay("push")
T.check("R4 push: exit 0, /state on the bus is {v:1, enc} and decrypts to the same document as the dry-run (but ts)", r.returncode == 0 and set(STORE.get("state", {})) == {"v", "enc"} and STORE["state"]["v"] == 1 and {kk: v for kk, v in C.decrypt(STORE["state"], k).items() if kk != "ts"} == {kk: v for kk, v in dry.items() if kk != "ts"}, r.stdout + r.stderr + str(STORE.get("state"))[:100])
evs = {kk: C.decrypt(v, k) for kk, v in (STORE.get("events") or {}).items()}
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
evs = {kk: C.decrypt(v, k) for kk, v in STORE["events"].items()}
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

# R5: pair con un orologio finto
def pair_run(timeout=8):
    pr = subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "pair", "--timeout", str(timeout)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    line = pr.stdout.readline()
    import re as _re
    m = _re.search(r"\b(\d{6})\b", line)
    return pr, (m.group(1) if m else "")


pr, code = pair_run()
T.check("R5 pair: a six-digit code on stdout; /pair/<code> has the PC's public key, host and exp", len(code) == 6 and T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3) and STORE["pair"][code]["host"] == "crostini-test" and STORE["pair"][code]["exp"] > time.time(), str(STORE.get("pair")))
w_priv, w_pub = C.pair_keys()
k_watch = C.shared_key(w_priv, STORE["pair"][code]["pc_pub"])
for i in range(5):
    http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w_pub, "uid": "u-bad", "name": "intruder", "check": f"{i:016x}"})
    T.wait_until(lambda: not (STORE.get("pair") or {}).get(code, {}).get("watch"), 3)
pr.wait(timeout=15)
T.check("R5 five wrong checks → exit 2, no key change, no uid allowed, /pair cleaned", pr.returncode == 2 and C.load_key(rdir2) == k and not (STORE.get("allowed") or {}) and code not in (STORE.get("pair") or {}), f"rc={pr.returncode} " + pr.stdout.read() + pr.stderr.read())
pr, code = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
w_priv, w_pub = C.pair_keys()
k_watch = C.shared_key(w_priv, STORE["pair"][code]["pc_pub"])
http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w_pub, "uid": "u1", "name": "watch-pixel5", "check": C.check_code(k_watch, code)})
pr.wait(timeout=15)
out5 = pr.stdout.read()
T.check("R5 a new key wipes /events and /result on the bus (old ciphertext is unreadable)", "events" not in STORE and "result" not in STORE, str(list(STORE)))
T.check("R5 right check → exit 0, the shared key saved (0600) and equal to the watch's, u1 in /allowed and devices.json, /pair/<code> deleted, «accoppiato» printed", pr.returncode == 0 and C.load_key(rdir2) == k_watch and STORE.get("allowed") == {"u1": True} and "u1" in json.loads((rdir2 / "devices.json").read_text()) and code not in (STORE.get("pair") or {}) and "accoppiato" in out5, f"rc={pr.returncode} {out5} {pr.stderr.read()} {STORE.get('allowed')}")
pr, code = pair_run()
T.wait_until(lambda: (STORE.get("pair") or {}).get(code, {}).get("pc_pub"), 3)
w2_priv, w2_pub = C.pair_keys(); k2 = C.shared_key(w2_priv, STORE["pair"][code]["pc_pub"])
http("PUT", f"/pair/{code}/watch.json", {"watch_pub": w2_pub, "uid": "u2", "name": "watch-new", "check": C.check_code(k2, code)})
pr.wait(timeout=15)
T.check("R5 pairing again (a reset watch) → the old uid revoked: /allowed = {u2}, new key", pr.returncode == 0 and STORE.get("allowed") == {"u2": True} and C.load_key(rdir2) == k2, str(STORE.get("allowed")))
pr, code = pair_run(timeout=2)
pr.wait(timeout=15)
T.check("R5 nobody answers → exit 3 at the timeout, /pair cleaned", pr.returncode == 3 and code not in (STORE.get("pair") or {}), f"rc={pr.returncode}")
k = k2   # da qui la chiave viva e' quella dell'ultimo pairing

# R6: serve — i comandi del contratto
rows_alive("ledger-api", "atlas-shop", "field-notes")
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {}}))
relay("push")
cron = tmp / "crontab"; cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
ENV["CM_CRONTAB_CMD"] = str(fake_crontab)


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
T.check("R6 answer → `answer work-ledger-api 1` (name mapped to tmux), /result {ok, text «answered 1. yes», at}, /cmd/<id> deleted", res and res["ok"] is True and res["text"] == "answered 1. yes" and isinstance(res["at"], int) and "answer work-ledger-api 1" in cm_calls() and CMDS[0]["id"] not in (STORE.get("cmd") or {}), str(res) + str(cm_calls()[-4:]))
T.check("R6 a successful answer removes the hook's waiting flag (else «waiting» until the next prompt: answered and outcome 3 min late, 14/09)", not (state_dir / "waiting" / "S-L").exists(), str(list((state_dir / "waiting").iterdir())))
led_rows = lambda: [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]  # noqa: E731
T.check("R6 the command is annotated in the ledger: event watch-cmd with op, name, by (who answered) and ok", any(x.get("event") == "watch-cmd" and x.get("op") == "answer" and x.get("name") == "ledger-api" and x.get("by") == "watch-pixel5" and x.get("ok") is True for x in led_rows()), str([x for x in led_rows() if x.get("event") == "watch-cmd"][-2:]))
T.check("R6 after a command /state is republished (a new PUT of /state)", T.wait_until(lambda: len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) > n_state_puts, 6), "")
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
T.check("R6 follow → follow.json has atlas-shop, «following atlas-shop»; the next /state marks it followed", res and res["ok"] and res["text"] == "following atlas-shop" and "atlas-shop" in json.loads((rdir2 / "follow.json").read_text()) and T.wait_until(lambda: any(s_["name"] == "atlas-shop" and s_["followed"] for s_ in C.decrypt(STORE["state"], k)["sessions"]), 6), str(res))
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
def answered_count(name):
    return sum(1 for v in (STORE.get("events") or {}).values() if (lambda e: e.get("kind") == "answered" and e.get("session") == name)(C.decrypt(v, k) or {}))


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

T.finish()

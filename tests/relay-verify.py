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
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

WATCH = Path.home() / "Desktop" / "workspaces" / "personali" / "claude-master-watch"
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


ROOT_WS = "/home/franz/Desktop/workspaces"
F1 = json.loads((FIX / "state-1-question.json").read_text())
F2 = json.loads((FIX / "state-2-idle.json").read_text())
F3 = json.loads((FIX / "state-3-stale.json").read_text())
SRC1 = {
    "host": "crostini-franz", "root": ROOT_WS, "prefixes": ["pix-"], "high_words": None,
    "rows": [
        {"name": "pix-ledger-api", "tmux": "pix-ledger-api", "account": "agenzia", "cwd": ROOT_WS + "/pixelfarm/clienti/ledger-api", "status": "waiting", "waiting": True, "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "link": "https://claude.ai/code/session_01CnGG8im7UG4KtjbPDx9fst", "attached": False, "started_at": 1789210000000},
        {"name": "atlas-shop", "tmux": "atlas-shop", "account": "personale", "cwd": ROOT_WS + "/personali/atlas-shop", "status": "busy", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": True, "started_at": 1789209000000},
        {"name": "field-notes", "tmux": "field-notes", "account": "personale", "cwd": ROOT_WS + "/personali/field-notes", "status": "idle", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "link": "https://claude.ai/code/session_03fieldnotes", "attached": False, "started_at": 1789120000000},
        {"name": "pix-orbit-docs", "tmux": "pix-orbit-docs", "account": "agenzia", "cwd": ROOT_WS + "/pixelfarm/nostri/orbit-docs", "status": "dead", "waiting": False, "session_id": "3d1b2c4e-0000-4000-8000-000000000004", "link": "", "attached": False, "visto_ts": 1789200000},
    ],
    "ledger": [
        {"event": "start", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210000)},
        {"event": "prompt", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210380)},
        {"event": "waiting", "session_id": "9e9c87fb-edcd-4c51-8c62-328c0146019b", "ts": iso(1789210500), "tool": "AskUserQuestion"},
        {"event": "start", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789209000)},
        {"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789210300), "last": "x", "esito": "Esito: migrazioni 008-011 applicate, test verdi.", "tail": "Esito: migrazioni 008-011 applicate, test verdi.\nRestano da rivedere i seed di prova e la pagina admin.\nWatch: Migrazioni 008-011 applicate, test verdi", "watch": "Watch: Migrazioni 008-011 applicate, test verdi"},
        {"event": "prompt", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789210700)},
        {"event": "start", "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "ts": iso(1789120000)},
        {"event": "stop", "session_id": "3d1b2c4e-0000-4000-8000-000000000003", "ts": iso(1789121000), "last": "x", "esito": "Esito: README riscritto con le tre sezioni chieste.", "tail": "Esito: README riscritto con le tre sezioni chieste.\nWatch: README riscritto", "watch": "Watch: README riscritto"},
    ],
    "questions": {"pix-ledger-api": {"tool": "AskUserQuestion", "text": "Deploy ready, waiting for the client's ok. Deploy now?", "options": ["yes", "no"], "asked_at": 1789210500}},
    "quota": {"personale": {"cinque_ore_pct": 11, "settimana_pct": 36, "reset_settimanale": 1789610400, "vecchia": False},
              "agenzia": {"cinque_ore_pct": None, "settimana_pct": 75.2, "reset_settimanale": 1789444800, "vecchia": True}},
    "projects": [{"path": ROOT_WS + "/pixelfarm/nostri/orbit-docs", "name": "orbit-docs", "account": "agenzia"}, {"path": ROOT_WS + "/personali/atlas-shop", "name": "atlas-shop", "account": "personale"}, {"path": ROOT_WS + "/pixelfarm/clienti/ledger-api", "name": "ledger-api", "account": "agenzia"}],
    "night": {"queued": 2, "running": None},
    "recap": {"date": "2026-09-12", "items": [{"project": "atlas-shop", "done": "Migrazioni 008-011 applicate, test verdi", "next": "Rivedere i seed e la pagina admin"}, {"project": "ledger-api", "done": "Deploy pronto", "next": "Wait for the go"}]},
    "follow": {"pix-ledger-api"}, "awaiting": set(),
    "next": {"pix-ledger-api": "Wait for the go", "atlas-shop": "Rivedere i seed e la pagina admin", "pix-orbit-docs": "Riprendere la pagina prezzi"},
    "tools": {"atlas-shop": "Bash pytest -q tests"},
}
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
T.check("R2 rules: order waiting/busy/idle/gone then alphabetical, short ≤ 60, full ≤ 600, n from 1, ≤ 8 KB", [x["state"] for x in st1["sessions"]] == ["waiting", "busy", "idle", "gone"] and all(len(x["outcome"]["short"]) <= 60 and len(x["outcome"]["full"]) <= 600 for x in st1["sessions"] if x["outcome"]) and [o["n"] for o in st1["sessions"][0]["question"]["options"]] == [1, 2] and S.size_of(st1) <= 8192, str(S.size_of(st1)))
SRC2 = {"host": "crostini-franz", "root": ROOT_WS, "prefixes": ["pix-"],
        "rows": [{"name": "atlas-shop", "tmux": "atlas-shop", "account": "personale", "cwd": ROOT_WS + "/personali/atlas-shop", "status": "idle", "waiting": False, "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "link": "https://claude.ai/code/session_018CKZ1Pum1Qs7DX5hbRLQ6X", "attached": False, "started_at": 1789214000000}],
        "ledger": [{"event": "stop", "session_id": "f61903c0-ea6a-409c-a961-d01126a0f3ad", "ts": iso(1789213900), "last": "x", "esito": "Esito: seed e pagina admin rivisti, 42 test verdi.", "tail": "Esito: seed e pagina admin rivisti, 42 test verdi.\nWatch: Seed e pagina admin rivisti", "watch": "Watch: Seed e pagina admin rivisti"}],
        "questions": {}, "quota": {"personale": {"cinque_ore_pct": 24, "settimana_pct": 38, "reset_settimanale": 1789610400, "vecchia": False}, "agenzia": {"cinque_ore_pct": 3, "settimana_pct": 75, "reset_settimanale": 1789444800, "vecchia": False}},
        "projects": [{"path": ROOT_WS + "/personali/atlas-shop", "name": "atlas-shop", "account": "personale"}], "night": {"queued": 0, "running": None}, "recap": {"date": "2026-09-12", "items": []},
        "follow": set(), "awaiting": set(), "next": {"atlas-shop": "Deploy di prova su staging"}, "tools": {}}
st2 = S.build_state(SRC2, 1789214400)
T.check("R2 build_state(src) == state-2-idle.json", st2 == F2, diff(st2, F2) or "equal")
SRC3 = {"host": "crostini-franz", "root": ROOT_WS, "prefixes": [], "rows": [], "ledger": [], "questions": {},
        "quota": {"personale": {"cinque_ore_pct": 0, "settimana_pct": 36, "reset_settimanale": 1789610400, "vecchia": True}, "agenzia": {"cinque_ore_pct": None, "settimana_pct": 75, "reset_settimanale": 1789444800, "vecchia": True}},
        "projects": [], "night": {"queued": 0, "running": None}, "recap": {"date": "2026-09-12", "items": []}, "follow": set(), "awaiting": set(), "next": {}, "tools": {}}
st3 = S.build_state(SRC3, 1789200000)
T.check("R2 build_state(src) == state-3-stale.json", st3 == F3, diff(st3, F3) or "equal")
big = dict(SRC1); big["projects"] = [{"path": f"{ROOT_WS}/personali/p{i:03d}", "name": f"p{i:03d}", "account": "personale"} for i in range(120)]
stb = S.build_state(big, 1789210800)
T.check("R2 over 8 KB → fit_state trims (recap items, then outcome.full, then projects past 10), the question stays whole", S.size_of(stb) <= 8192 and stb["sessions"][0]["question"]["text"] == F1["sessions"][0]["question"]["text"] and len(stb["projects"]) <= 10, str(S.size_of(stb)))
T.check("R2 tier_of: dangerous words in a PERMISSION → high; Read → low; Bash → medium; ask/plan → always medium", S.tier_of("permission", "Bash", "rm -rf build") == "high" and S.tier_of("permission", "Read", "cat x") == "low" and S.tier_of("permission", "Bash", "ls") == "medium" and S.tier_of("ask", "AskUserQuestion", "Deploy now?") == "medium" and S.tier_of("ask", None, "git push origin main?") == "medium" and S.tier_of("permission", "Bash", "git push origin main") == "high", "")
T.check("R2 awaiting state: a session with a wrist prompt pending is «awaiting» (ordered with busy)", S.state_of({"status": "idle", "tmux": "x"}, {"x"}) == "awaiting" and S.ORDER["awaiting"] == S.ORDER["busy"], "")

# R3: eventi dal diff
EV = json.loads((FIX / "events-sample.json").read_text())
ev, seq = S.events_between(F2, F1, 1789210800, 1)
T.check("R3 state-2 → state-1: launched ledger-api, question ledger-api, outcome atlas-shop, launched field-notes; keys <ts>_<seq>; shape of events-sample", [(e["kind"], e["session"]) for e in ev] == [("launched", "ledger-api"), ("question", "ledger-api"), ("outcome", "atlas-shop"), ("launched", "field-notes")] and ev[0]["key"] == "1789210800_001" and ev[3]["key"] == "1789210800_004" and seq == 5 and all(set(e) == set(EV[0]) for e in ev) and ev[1]["title"] == "❓ ledger-api" and ev[1]["body"] == F1["sessions"][0]["question"]["text"] and ev[1]["ref"] == "q-1789210500-1" and ev[2]["body"] == "Migrazioni 008-011 applicate, test verdi" and ev[0]["body"] == "pixelfarm/clienti/ledger-api", str(ev))
ev2, _ = S.events_between(F1, F2, 1789214400, 1)
T.check("R3 state-1 → state-2: outcome atlas-shop (new at), gone ledger-api and field-notes (vanished), nothing for orbit-docs (already gone)", [(e["kind"], e["session"]) for e in ev2] == [("outcome", "atlas-shop"), ("gone", "ledger-api"), ("gone", "field-notes")] and ev2[1]["title"] == "✗ ledger-api", str(ev2))
q_prev = {"sessions": [F1["sessions"][0]], "quota": {"personale": {"h5": 90, "w7": 30, "reset_w7": 1789610400, "stale": False}}}
q_cur = {"sessions": [dict(F1["sessions"][0], question=None)], "quota": {"personale": {"h5": 96, "w7": 30, "reset_w7": 1789610400, "stale": False}}}
ev3, _ = S.events_between(q_prev, q_cur, 1789210900, 7)
T.check("R3 question gone → answered (ref = question id); quota crossing warn_pct → «⚠ 96 % personale» with the reset time", [(e["kind"], e["key"]) for e in ev3] == [("answered", "1789210900_007"), ("quota", "1789210900_008")] and ev3[0]["ref"] == "q-1789210500-1" and ev3[1]["title"] == "⚠ 96 % personale" and ev3[1]["account"] == "personale" and ev3[1]["body"].startswith("reset "), str(ev3))

# R4: relay push contro il Firebase finto, con dispatcher finto e stato finto della macchina
home = tmp / "home"; (home / ".claude" / "waiting").mkdir(parents=True)
ws = home / "ws"
for d in ("personali/atlas-shop/docs", "personali/field-notes", "pixelfarm/clienti/ledger-api", "pixelfarm/nostri/orbit-docs", ".claude"):
    (ws / d).mkdir(parents=True)
(ws / "personali" / "atlas-shop" / "docs" / "recap.md").write_text("# Recap\n\n- 2026-09-12: Migrazioni applicate · prossimo: Rivedere i seed e la pagina admin\n")
state_dir = home / ".claude"
argslog = tmp / "cm-args.log"
alive = tmp / "alive.json"
fake_cm = tmp / "claude-master"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  sessions) cat "{alive}" ;;
  registry) echo '{{"sessioni": [{{"nome": "pix-orbit-docs", "cartella": "{ws / 'pixelfarm' / 'nostri' / 'orbit-docs'}", "account": "agenzia", "visto": "2026-09-12T09:00:00"}}]}}' ;;
  quota) echo '{{"personale": {{"cinque_ore_pct": 11, "settimana_pct": 36, "reset_settimanale": 1789610400, "vecchia": false}}, "agenzia": {{"cinque_ore_pct": null, "settimana_pct": 75, "reset_settimanale": 1789444800, "vecchia": true}}}}' ;;
  answer) if [ "$3" = "--show" ]; then if [ "$2" = "pix-ledger-api" ]; then echo "«$2» chiede — Deploy: Deploy ready, waiting for the client ok. Deploy now?"; echo "  ❯ 1. yes"; echo "    2. no"; else echo "nessuna domanda aperta sullo schermo"; exit 1; fi; else case "$3" in 1|2) echo "«$2»: risposto $3. yes  (Deploy now?)" ;; *) echo "opzione $3 inesistente" >&2; exit 2 ;; esac; fi ;;
  screen) i=1; while [ $i -le 30 ]; do echo "riga $i dello schermo"; i=$((i+1)); done ;;
  talk) echo "consegnato" ;;
  launch) echo "sessione avviata"; echo "  link: https://claude.ai/code/session_01NEW" ;;
esac
""")
fake_cm.chmod(0o755)


def rows_alive(*names, **over):
    base = {"ledger-api": {"pid": 7, "name": "pix-ledger-api", "tmux": "pix-ledger-api", "cwd": str(ws / "pixelfarm" / "clienti" / "ledger-api"), "status": "waiting", "waiting": True, "link": "https://claude.ai/code/session_01L", "account": "agenzia", "session_id": "S-L", "attached": False, "started_at": 1789210000000},
            "atlas-shop": {"pid": 8, "name": "atlas-shop", "tmux": "atlas-shop", "cwd": str(ws / "personali" / "atlas-shop"), "status": "busy", "waiting": False, "link": "https://claude.ai/code/session_01A", "account": "personale", "session_id": "S-A", "attached": True, "started_at": 1789209000000},
            "field-notes": {"pid": 9, "name": "field-notes", "tmux": "field-notes", "cwd": str(ws / "personali" / "field-notes"), "status": "idle", "waiting": False, "link": "https://claude.ai/code/session_01F", "account": "personale", "session_id": "S-F", "attached": False, "started_at": 1789120000000}}
    rows = [dict(base[n], **over.get(n, {})) for n in names]
    alive.write_text(json.dumps(rows))


rows_alive("ledger-api", "atlas-shop", "field-notes")
(state_dir / "waiting" / "S-L").write_text(json.dumps({"tool": "AskUserQuestion", "input": {"questions": [{"question": "Deploy now?"}]}}))
ledger = state_dir / "ledger.jsonl"
ledger.write_text("\n".join(json.dumps(r) for r in [
    {"ts": iso(1789210380), "event": "prompt", "session_id": "S-L"},
    {"ts": iso(1789210500), "event": "waiting", "session_id": "S-L", "tool": "AskUserQuestion"},
    {"ts": iso(1789210300), "event": "stop", "session_id": "S-A", "last": "x", "esito": "Esito: migrazioni 008-011 applicate, test verdi.", "tail": "Esito: migrazioni 008-011 applicate, test verdi.\nRestano da rivedere i seed.\nWatch: Migrazioni applicate, test verdi", "watch": "Watch: Migrazioni applicate, test verdi"},
    {"ts": iso(1789210700), "event": "prompt", "session_id": "S-A"},
]) + "\n")
rdir2 = tmp / "relay-live"
C.save_key(rdir2, k)
cfg = tmp / "config.json"


def write_cfg(enabled=True, **extra):
    d = {"language": "it", "state_dir": str(state_dir), "default_account": "personale",
         "workspace": {"root": str(ws), "excluded_dirs": [".git"], "project_dirs": ["personali", "pixelfarm/clienti", "pixelfarm/nostri"]},
         "folder_map": [{"path": str(ws / "pixelfarm"), "account": "agenzia"}, {"path": str(ws / "personali"), "account": "personale"}],
         "accounts": {"personale": {"config_dir": str(home / ".claude")}, "agenzia": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
         "bot": {"state_file": str(tmp / "bot-state.json")},
         "relay": {"enabled": enabled, "firebase_url": URL, "service_account": str(SA), "token_url": URL + "/token", "fcm_url": URL,
                   "dir": str(rdir2), "host": "crostini-test", "debounce_s": 1, "fcm_topic": "watch", **extra}}
    cfg.write_text(json.dumps(d))


write_cfg()
(tmp / "bot-state.json").write_text(json.dumps({"chats": {"1001": {"follow": ["pix-ledger-api"], "awaiting": {}}}}))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm)}


def relay(*args, timeout=60, env=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), *args], capture_output=True, text=True, env=env or ENV, timeout=timeout)


def cm_calls():
    return argslog.read_text().splitlines() if argslog.exists() else []


n_req = len(CALLS["requests"])
r = relay("push", "--dry-run")
dry = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("{") else {}
T.check("R4 push --dry-run: clear JSON on stdout, no HTTP; sessions ordered ❓ ▶ ✓ ✗ with short names, the question whole with kind ask and options 1-2, the busy session's outcome from the ledger (short = Watch line), gone from the snapshot, quota, projects with accounts from folder_map, recap, night", r.returncode == 0 and len(CALLS["requests"]) == n_req and [(x["name"], x["state"]) for x in dry.get("sessions", [])] == [("ledger-api", "waiting"), ("atlas-shop", "busy"), ("field-notes", "idle"), ("orbit-docs", "gone")] and dry["sessions"][0]["question"]["text"] == "Deploy ready, waiting for the client ok. Deploy now?" and dry["sessions"][0]["question"]["kind"] == "ask" and [o["label"] for o in dry["sessions"][0]["question"]["options"]] == ["yes", "no"] and dry["sessions"][0]["question"]["asked_at"] == 1789210500 and dry["sessions"][0]["followed"] is True and dry["sessions"][0]["project"] == "pixelfarm/clienti/ledger-api" and dry["sessions"][1]["outcome"]["short"] == "Migrazioni applicate, test verdi" and dry["sessions"][1]["turn_started"] == 1789210700 and dry["sessions"][1]["next"] == "Rivedere i seed e la pagina admin" and dry["sessions"][3]["since"] == S.epoch("2026-09-12T09:00:00") and dry["quota"]["agenzia"] == {"h5": None, "w7": 75, "reset_w7": 1789444800, "stale": True} and {(p["name"], p["account"]) for p in dry["projects"]} == {("atlas-shop", "personale"), ("field-notes", "personale"), ("ledger-api", "agenzia"), ("orbit-docs", "agenzia")} and dry["host"] == "crostini-test" and dry["night"] == {"queued": 0, "running": None} and dry["v"] == 1, r.stdout[:600] + r.stderr)
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
    (ws / "personali" / f"progetto-con-un-nome-lungo-{i:03d}").mkdir()
r = relay("push", "--dry-run")
big = json.loads(r.stdout)
T.check("R4 a state over 8 KB is trimmed under the cap (projects cut to 10), the question untouched", r.returncode == 0 and S.size_of(big) <= 8192 and len(big["projects"]) <= 10, str(S.size_of(big)))
import shutil
for i in range(150):
    shutil.rmtree(ws / "personali" / f"progetto-con-un-nome-lungo-{i:03d}")
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
pid1 = serve_pid()
CMDS = json.loads((FIX / "cmd-result-sample.json").read_text())["cmd"]


def send_cmd(cmd, wait=8):
    cid = cmd["id"]
    http("PUT", f"/cmd/{cid}.json", C.encrypt(cmd, k))
    T.wait_until(lambda: (STORE.get("result") or {}).get(cid), wait)
    res = STORE.get("result", {}).get(cid)
    return C.decrypt(res, k) if res else None


n_state_puts = len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")])
res = send_cmd(CMDS[0])   # answer ledger-api 1
T.check("R6 answer → `answer pix-ledger-api 1` (name mapped to tmux), /result {ok, text «answered 1. yes», at}, /cmd/<id> deleted", res and res["ok"] is True and res["text"] == "answered 1. yes" and isinstance(res["at"], int) and "answer pix-ledger-api 1" in cm_calls() and CMDS[0]["id"] not in (STORE.get("cmd") or {}), str(res) + str(cm_calls()[-4:]))
T.check("R6 after a command /state is republished (a new PUT of /state)", T.wait_until(lambda: len([x for x in CALLS["requests"] if x == ("PUT", "/state.json")]) > n_state_puts, 6), "")
res = send_cmd(CMDS[1])   # prompt atlas-shop
T.check("R6 prompt → talk atlas-shop with the watch prefix (Watch: line requested) --no-wait, «delivered», atlas-shop awaiting", res and res["ok"] and res["text"] == "delivered" and any(c.startswith("talk atlas-shop Da Franz via polso") and "Watch:" in c and c.endswith("rivedi i seed di prova --no-wait") for c in cm_calls()) and "atlas-shop" in json.loads((rdir2 / "awaiting.json").read_text()), str(res) + str(cm_calls()[-3:]))
res = send_cmd(CMDS[2])   # launch path fuori dai progetti pubblicati
T.check("R6 launch of a path outside the published projects → ok false, no launch", res and res["ok"] is False and "not a published project" in res["text"] and not any(c.startswith("launch ") for c in cm_calls()), str(res))
res = send_cmd(dict(CMDS[2], id="6f1c2d3e-0003-4000-8000-0000000000aa", arg=str(ws / "pixelfarm" / "nostri" / "orbit-docs")))
T.check("R6 launch of a published project → `launch PATH --no-window`, «launched orbit-docs (agenzia)»", res and res["ok"] is True and res["text"] == "launched orbit-docs (agenzia)" and any(c.startswith("launch ") and c.endswith("orbit-docs --no-window") for c in cm_calls()), str(res) + str(cm_calls()[-3:]))
res = send_cmd(CMDS[3])   # screen
T.check("R6 screen → `screen atlas-shop --lines 30`, 30 lines in text", res and res["ok"] and len(res["text"].splitlines()) == 30 and "screen atlas-shop --lines 30" in cm_calls(), str(res)[:200])
res = send_cmd(CMDS[4])   # follow
T.check("R6 follow → follow.json has atlas-shop, «following atlas-shop»; the next /state marks it followed", res and res["ok"] and res["text"] == "following atlas-shop" and "atlas-shop" in json.loads((rdir2 / "follow.json").read_text()) and T.wait_until(lambda: any(s_["name"] == "atlas-shop" and s_["followed"] for s_ in C.decrypt(STORE["state"], k)["sessions"]), 6), str(res))
res = send_cmd(dict(CMDS[4], id="6f1c2d3e-0005-4000-8000-0000000000bb", op="unfollow"))
T.check("R6 unfollow → removed", res and res["ok"] and "atlas-shop" not in json.loads((rdir2 / "follow.json").read_text()), str(res))
res = send_cmd(CMDS[5])   # resume orbit-docs (gone)
T.check("R6 resume of a gone session → ok false «orbit-docs is gone: use launch»", res and res["ok"] is False and res["text"] == "orbit-docs is gone: use launch", str(res))
res = send_cmd(CMDS[6])   # allow_all ledger-api
T.check("R6 allow_all without a «don't ask again» option → ok false with the contract's text", res and res["ok"] is False and res["text"] == "no «don't ask again» option on this question", str(res))
n_ans = len([c for c in cm_calls() if c == "answer pix-ledger-api 1"])
http("PUT", f"/cmd/{CMDS[0]['id']}.json", C.encrypt(CMDS[0], k))
time.sleep(2.5)
T.check("R6 the same id again → ignored (no second `answer`), /cmd cleaned", len([c for c in cm_calls() if c == "answer pix-ledger-api 1"]) == n_ans and CMDS[0]["id"] not in (STORE.get("cmd") or {}), str(cm_calls()[-3:]))
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
r = hook("PermissionRequest", {"session_id": "S-A", "cwd": str(ws / "personali" / "atlas-shop"), "tool_name": "Bash", "tool_input": {"command": "rm -rf build", "description": "clean"}})
wf = json.loads((state_dir / "waiting" / "S-A").read_text())
T.check("R7 PermissionRequest: waiting/<sid> is JSON {tool, input{command, description}}; a push follows (async) → a new PUT of /state", r.returncode == 0 and wf["tool"] == "Bash" and wf["input"]["command"] == "rm -rf build" and T.wait_until(lambda: state_puts() > n0, 6), r.stdout + r.stderr + str(wf))
r = relay("push", "--dry-run")
dry7 = json.loads(r.stdout)
a7 = next(s_ for s_ in dry7["sessions"] if s_["name"] == "atlas-shop")
T.check("R7 …and /state now shows atlas-shop waiting on a permission with tier high (rm -rf) and the question text from the screen", a7["state"] == "waiting" and a7["question"]["kind"] == "permission" and a7["question"]["tier"] == "high", str(a7["question"]))
(state_dir / "waiting" / "S-A").unlink()
r = hook("PermissionRequest", {"session_id": "S-F", "cwd": str(ws / "personali" / "field-notes"), "tool_name": "AskUserQuestion",
                               "tool_input": {"questions": [{"question": "Procedo con il deploy di prova?", "header": "Deploy", "options": [{"label": "Sì", "description": "vai"}, {"label": "No", "description": "aspetta"}]}]}})
wf2 = json.loads((state_dir / "waiting" / "S-F").read_text())
r = relay("push", "--dry-run"); dry7b = json.loads(r.stdout)
f7 = next(s_ for s_ in dry7b["sessions"] if s_["name"] == "field-notes")
T.check("R7b AskUserQuestion before the dialog is on screen (answer --show finds nothing): waiting/<sid> keeps question + option labels from the payload, and /state carries text and options 1-2 with kind ask", wf2["input"]["question"] == "Procedo con il deploy di prova?" and wf2["input"]["options"] == ["Sì", "No"] and f7["state"] == "waiting" and f7["question"]["text"] == "Procedo con il deploy di prova?" and [o["label"] for o in f7["question"]["options"]] == ["Sì", "No"] and f7["question"]["kind"] == "ask", str(wf2) + str(f7["question"]))
(state_dir / "waiting" / "S-F").unlink()
n0 = state_puts()
r = hook("UserPromptSubmit", {"session_id": "S-A", "cwd": str(ws / "personali" / "atlas-shop"), "prompt": "vai"})
T.check("R7 UserPromptSubmit: a «prompt» row in the ledger (turn_started), no push", r.returncode == 0 and any(json.loads(l).get("event") == "prompt" and json.loads(l).get("session_id") == "S-A" for l in ledger.read_text().splitlines()) and state_puts() == n0, ledger.read_text()[-200:])
n0 = state_puts()
r = hook("Stop", {"session_id": "S-A", "cwd": str(ws / "personali" / "atlas-shop"), "last_assistant_message": "Fatto.\nEsito: seed rivisti.\nWatch: Seed rivisti"})
T.check("R7 Stop: ledger row with watch, then a push", r.returncode == 0 and T.wait_until(lambda: state_puts() > n0, 6) and any(json.loads(l).get("watch") == "Watch: Seed rivisti" for l in ledger.read_text().splitlines()), r.stdout + r.stderr)
n0 = state_puts()
hook("SessionEnd", {"session_id": "S-F", "cwd": str(ws / "personali" / "field-notes"), "reason": "other"})
T.check("R7 SessionEnd → push", T.wait_until(lambda: state_puts() > n0, 6), "")
write_cfg(enabled=False)
time.sleep(5)   # le push asincrone precedenti (con i 3 s di attesa del dialogo) devono essersi esaurite
n0 = state_puts()
r = hook("Stop", {"session_id": "S-A", "cwd": str(ws / "personali" / "atlas-shop"), "last_assistant_message": "x"})
time.sleep(2.5)
T.check("R7 relay.enabled=false: the hook does not push", r.returncode == 0 and state_puts() == n0, "")
write_cfg()
os.environ.update({"CLAUDE_MASTER_CONFIG": str(cfg), "HOME": str(home), "CM_RELAY_CM": str(fake_cm)})
bot = load("cm-bot")
n0 = state_puts()
cs7 = {"follow": []}
txt = bot.toggle_follow(cs7, "atlas-shop")
T.check("R7 bot toggle_follow → push (a new PUT of /state)", "atlas-shop" in cs7["follow"] and T.wait_until(lambda: state_puts() > n0, 6), txt)

T.finish()

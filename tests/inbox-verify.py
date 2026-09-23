#!/usr/bin/env python3
"""Verifica la casella persistente (cm-inbox.py, talk, cm-hook) — 23/09/2026.

IB1 un messaggio in attesa: consegnato al SessionStart della destinataria (testo, mittente), poi non piu'
IB2 stesso nome tmux in un'altra cartella: niente consegna
IB3 scaduto (48 h): non si consegna, stato expired
IB4 oltre 5 messaggi: se ne mostrano 5, gli altri restano in attesa e il testo dice come leggerli
IB5 talk verso una sessione nota (registro) ma chiusa: exit 0, salvato; verso un nome mai visto: exit 3, niente salvato
IB6 cm-hook: SessionStart consegna nel contesto; Stop con messaggi in attesa → decision block; con stop_hook_active → niente
IB7 inbox status / talk --status
IB8 registro: righe talk e delivered nel diario, con la sola prima riga del testo
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
work = home / "ws" / "alfa"
work.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
cfg = tmp / "config.json"
good = state / "sessions-good.json"
good.write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "chiusa", "cartella": str(work), "account": "personale"}]}))
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "default_account": "personale",
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "registry": {"good_file": str(good), "file": str(state / "sessions.json")},
                           "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}}}))


def env(extra=None):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
         "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": ""}
    e.update(extra or {})
    return e


def py(code):
    """Esegue codice con il modulo cm-inbox caricato come `I`."""
    pre = ("import importlib.util,sys,json;s=importlib.util.spec_from_file_location('i',sys.argv[1]);I=importlib.util.module_from_spec(s);"
           "s.loader.exec_module(I);")
    return subprocess.run([sys.executable, "-c", pre + code, str(T.SCRIPTS / "cm-inbox.py")], capture_output=True, text=True, env=env(), timeout=30)


def recs(to):
    return [json.loads(p.read_text()) for p in sorted((state / "inbox" / to).glob("*.json"))] if (state / "inbox" / to).exists() else []


def hook(ev, payload, extra=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True,
                          env=env(extra), timeout=60)


# IB1-IB4 con il modulo
py(f"I.put('alfa','Risposta: la release e\\' pubblicata.\\nSeconda riga.','master',{str(work)!r})")
b1 = py(f"print(I.deliver_block('alfa',{str(work)!r},'session-start'))").stdout
b2 = py(f"print(I.deliver_block('alfa',{str(work)!r},'session-start'))").stdout.strip()
T.check("IB1 a pending message is delivered once, with sender and text; then nothing", "da master" in b1 and "la release e' pubblicata" in b1
        and "Seconda riga." in b1 and b2 == "" and recs("alfa")[0]["status"] == "delivered", b1 + "|" + b2)
py(f"I.put('alfa','per la vecchia alfa','master',{str(work)!r})")
other = home / "ws" / "altrove"
other.mkdir()
b3 = py(f"print(I.deliver_block('alfa',{str(other)!r},'session-start'))").stdout.strip()
T.check("IB2 same tmux name in another folder: not delivered, still pending", b3 == "" and any(r["status"] == "pending" for r in recs("alfa")), b3)
py("r=I.put('beta','vecchio','master');import json,os;p=I.box('beta')/(r['id']+'.json');d=json.loads(p.read_text());d['expires']=1;p.write_text(json.dumps(d))")
b4 = py("print(I.deliver_block('beta','','session-start'))").stdout.strip()
T.check("IB3 an expired message (past 48 h) is not delivered: status expired", b4 == "" and recs("beta")[0]["status"] == "expired", b4)
py("[I.put('gamma',f'messaggio numero {i}','master') for i in range(7)]")
b5 = py("print(I.deliver_block('gamma','','stop'))").stdout
T.check("IB4 over five messages: five shown, the rest still pending and the text says how to read them",
        b5.count("— da master") == 5 and "altri 2" in b5 and "claude-master inbox gamma" in b5
        and sum(1 for r in recs("gamma") if r["status"] == "pending") == 2, b5[-300:])

# IB5 talk
t1 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "chiusa", "ciao dalla prova"], capture_output=True, text=True,
                    env=env({"CM_TMUX_ARGS": "-L cm-inbox-none"}), timeout=60)
t2 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "mai-vista", "ciao"], capture_output=True, text=True,
                    env=env({"CM_TMUX_ARGS": "-L cm-inbox-none"}), timeout=60)
saved = recs("chiusa")
T.check("IB5 talk to a known but closed session → exit 0, saved with its folder; an unknown name → exit 3, nothing saved",
        t1.returncode == 0 and "salvato nella casella" in t1.stderr and len(saved) == 1 and saved[0]["to_cwd"] == os.path.realpath(work)
        and t2.returncode == 3 and not (state / "inbox" / "mai-vista").exists(), t1.stderr + t2.stderr)

# IB6 hook, in un tmux privato: il nome della sessione e' «chiusa»
with T.PrivateTmux() as tm:
    tm("new-session", "-d", "-s", "chiusa", "-x", "100", "-y", "20", "sleep 60")
    pane = tm("list-panes", "-t", "chiusa", "-F", "#{pane_id}").stdout.strip()
    E = {**tm.env, "TMUX_PANE": pane}
    s1 = hook("SessionStart", {"session_id": "S1", "cwd": str(work), "source": "resume"}, E)
    T.check("IB6 SessionStart of the recipient (tmux name + folder) puts the pending message in its context",
            "ciao dalla prova" in s1.stdout and "MESSAGGI ARRIVATI" in s1.stdout and recs("chiusa")[0]["status"] == "delivered", s1.stdout[-400:] + s1.stderr)
    py(f"I.put('chiusa','arrivato mentre lavorava','master',{str(work)!r})")
    s2 = hook("Stop", {"session_id": "S1", "cwd": str(work), "stop_hook_active": True, "last_assistant_message": "x"}, E)
    s3 = hook("Stop", {"session_id": "S1", "cwd": str(work), "stop_hook_active": False, "last_assistant_message": "x"}, E)
    blk = json.loads(s3.stdout.strip().splitlines()[-1]) if s3.stdout.strip() else {}
    T.check("IB6 Stop: with stop_hook_active nothing; otherwise a decision block carrying the message, then delivered",
            "arrivato mentre lavorava" not in s2.stdout and blk.get("decision") == "block" and "arrivato mentre lavorava" in blk.get("reason", "")
            and all(r["status"] == "delivered" for r in recs("chiusa")), s2.stdout + "|" + s3.stdout[-300:])

# IB7
rid = recs("chiusa")[0]["id"]
st = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "--status", rid], capture_output=True, text=True, env=env(), timeout=30)
lst = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-inbox.py"), "gamma"], capture_output=True, text=True, env=env(), timeout=30)
T.check("IB7 talk --status ID says delivered and how; inbox NAME lists the pending ones", f"messaggio {rid}" in st.stdout and "delivered" in st.stdout
        and ("via session-start" in st.stdout or "via stop" in st.stdout) and lst.stdout.count("pending") == 2, st.stdout + st.stderr + lst.stdout)

# IB8
led = [json.loads(l) for l in (state / "ledger.jsonl").read_text().splitlines() if l.strip()]
talks = [r for r in led if r["event"] == "talk"]
T.check("IB8 the activity log: talk rows (sender, recipient, id, first line only) and delivered rows",
        any(r["to"] == "alfa" and r["text"] == "Risposta: la release e' pubblicata." for r in talks) and any(r["event"] == "delivered" for r in led)
        and not any("Seconda riga" in json.dumps(r) for r in led), json.dumps(talks[:2]))
T.finish()

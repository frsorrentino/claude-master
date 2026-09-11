#!/usr/bin/env python3
"""Verifica cm-answer.py col claude finto (scenario question2: due domande di fila) su un tmux privato.

A1  --show: domanda e opzioni numerate, cursore sulla 1
A2  answer 3: Giù x2 + Invio → «→ verde», compare la seconda domanda; answer 2 sulla seconda → «→ M»
A3  senza domanda aperta: --show e answer rifiutano senza toccare tasti; opzione inesistente rifiutata
A4  --notify (payload PermissionRequest su stdin, TMUX_PANE della sessione): messaggio Telegram alle chat
    autorizzate con domanda, opzioni numerate dallo schermo e «rispondi N a NOME»; riga nel ledger
A5  --notify senza tmux (TMUX_PANE assente): domanda e opzioni dal tool_input del payload, nome dalla cartella,
    nessun «rispondi»; un permesso (tool diverso) → nome del tool e dettaglio
A6  --notify senza token Telegram → esce 0 senza chiamare l'API
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
cfg = tmp / "config.json"
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": ["1001", "1002"]}))
API, CALLS, _ = T.fake_telegram()
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"),
                           "bot": {"api_base": API, "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
                           "hooks": {"ask_notify": {"delay_s": 0.5}}}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(tm):
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}


def answer(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-answer.py"), *args], capture_output=True, text=True, env=env(tm), timeout=60)


def notify(tm, payload, **extra):
    e = env(tm) if tm else {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg)}
    e.update(extra)
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-answer.py"), "--notify"], input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=60)


def texts():
    return [c.get("text", "") for c in CALLS["sendMessage"]]


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "-x", "120", "-y", "40",
                    "env", "FAKE_CLAUDE_SCENARIO=question2", "FAKE_CLAUDE_REGISTER=0", str(FAKE)], env=env(tm), check=True)
    time.sleep(2)
    r = answer(tm, "alfa", "--show")
    T.check("A1 --show: question, four numbered options, cursor on 1", r.returncode == 0 and "colore preferito?" in r.stdout and "❯ 1. rosso" in r.stdout and "  4. Type something." in r.stdout, r.stdout + r.stderr)
    r = answer(tm, "alfa", "3")
    scr = subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-t", "alfa"], capture_output=True, text=True).stdout
    T.check("A2 answer 3 → verde chosen, second question on screen", r.returncode == 0 and "risposto 3. verde" in r.stdout and "taglia?" in scr, r.stdout + r.stderr + scr)
    T.check("A2 the leftover question is shown after answering", "un'altra domanda" in r.stdout and "❯ 1. S" in r.stdout, r.stdout)
    r = answer(tm, "alfa", "2")
    scr = subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-t", "alfa"], capture_output=True, text=True).stdout
    T.check("A2 answer 2 on the second question → M, no more questions", r.returncode == 0 and "risposto 2. M" in r.stdout and "→ M" in scr and "Enter to select" not in scr, r.stdout + scr)
    r = answer(tm, "alfa", "--show")
    T.check("A3 no open question: --show says so, exit 1", r.returncode == 1 and "nessuna domanda" in r.stdout, r.stdout + r.stderr)
    r = answer(tm, "alfa", "1")
    T.check("A3 no open question: answer refuses, no keys sent", r.returncode == 1 and "nessuna domanda" in r.stdout, r.stdout + r.stderr)
    r = answer(tm, "nessuna", "1")
    T.check("A3 unknown session refused", r.returncode == 1 and "nessuna sessione" in r.stderr, r.stdout + r.stderr)
    # A4: una nuova sessione col claude finto ferma sulla domanda; --notify come lo lancerebbe l'hook
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "beta", "-x", "120", "-y", "40",
                    "env", "FAKE_CLAUDE_SCENARIO=question", "FAKE_CLAUDE_REGISTER=0", str(FAKE)], env=env(tm), check=True)
    time.sleep(2)
    pane = tm("display-message", "-p", "-t", "beta", "#{pane_id}").stdout.strip()
    CALLS["sendMessage"].clear()
    r = notify(tm, {"session_id": "sid-beta", "cwd": str(home / "beta"), "tool_name": "AskUserQuestion",
                    "tool_input": {"questions": [{"question": "colore preferito?", "header": "Colore",
                                                  "options": [{"label": "rosso"}, {"label": "blu"}, {"label": "verde"}]}]}},
               TMUX_PANE=pane)
    t = texts()
    T.check("A4 --notify: one message per allowed chat, exit 0", r.returncode == 0 and len(t) == 2 and {c.get("chat_id") for c in CALLS["sendMessage"]} == {"1001", "1002"}, r.stdout + r.stderr + str(CALLS["sendMessage"]))
    T.check("A4 message: name, question, numbered options from the screen (incl. Type something.)", t and "beta" in t[0] and "colore preferito?" in t[0] and "1. rosso" in t[0] and "4. Type something." in t[0], t[0] if t else "-")
    T.check("A4 message: how to answer («N a beta»)", t and "a beta" in t[0] and "answer beta" in t[0], t[0] if t else "-")
    led = (tmp / "state" / "ledger.jsonl")
    T.check("A4 ledger row ask-notified", led.is_file() and '"ask-notified"' in led.read_text() and '"beta"' in led.read_text(), led.read_text() if led.is_file() else "-")
    scr = subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-t", "beta"], capture_output=True, text=True).stdout
    T.check("A4 no key sent: the question is still open", "Enter to select" in scr and "❯ 1. rosso" in scr, scr)
# A5: senza tmux → dal payload
CALLS["sendMessage"].clear()
r = notify(None, {"session_id": "sid-x", "cwd": str(home / "ws" / "gamma"), "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": "quale taglia?", "header": "Taglia",
                                                "options": [{"label": "S", "description": "piccola"}, {"label": "M"}]}]}})
t = texts()
T.check("A5 no tmux: question and options from the payload, folder name, no «rispondi»", r.returncode == 0 and len(t) == 2 and "gamma" in t[0] and "quale taglia?" in t[0] and "1. S" in t[0] and "2. M" in t[0] and "rispondi" not in t[0], r.stdout + r.stderr + (t[0] if t else "-"))
CALLS["sendMessage"].clear()
r = notify(None, {"session_id": "sid-y", "cwd": str(home / "ws" / "gamma"), "tool_name": "Bash",
                  "tool_input": {"command": "rm -rf build", "description": "Remove build dir"}})
t = texts()
T.check("A5 permission for another tool: tool name and detail", r.returncode == 0 and t and "gamma" in t[0] and "Bash" in t[0] and "rm -rf build" in t[0], r.stdout + r.stderr + (t[0] if t else "-"))
# A6: senza token → niente
CALLS["sendMessage"].clear()
(tg / ".env").write_text("")
r = notify(None, {"session_id": "sid-z", "cwd": str(home), "tool_name": "AskUserQuestion", "tool_input": {}})
T.check("A6 no token: exit 0, no API call", r.returncode == 0 and not CALLS["sendMessage"], r.stdout + r.stderr)
T.rm(tmp)
T.finish()

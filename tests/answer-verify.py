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
    T.check("A1 --show: question, three numbered options (the footers «Type something.» / «Chat about this» are not options), cursor on 1", r.returncode == 0 and "colore preferito?" in r.stdout and "❯ 1. rosso" in r.stdout and "3. verde" in r.stdout and "Type something." not in r.stdout and "Chat about" not in r.stdout, r.stdout + r.stderr)
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
    T.check("A4 message: name, question, numbered options from the screen (footers excluded), «n label · descrizione»", t and "beta" in t[0] and "colore preferito?" in t[0] and "1 rosso · descrizione 1" in t[0] and "Type something." not in t[0], t[0] if t else "-")
    T.check("A4 message: how to answer («2 a beta»), cut to the wrist width", t and "2 a beta" in t[0], t[0] if t else "-")
    # A4b (polso, 11/09 16:36): resa compatta, bottoni con le opzioni (ans:beta:N) + riga fissa, stato del bot in scheda
    m4 = CALLS["sendMessage"][0]
    kb4 = json.loads(m4.get("reply_markup") or "{}").get("inline_keyboard") or []
    T.check("A4b notice: first line «❓ 🔴 beta · Colore» (icon, name, header: the widest line first), whole lines, no «…», NOT silent", t[0].splitlines()[0].startswith("❓ ") and t[0].splitlines()[0].endswith(" beta · Colore") and "…" not in t[0] and m4.get("disable_notification") != "true", t[0])
    T.check("A4b option buttons ans:beta:N then «Apri … beta»; 3 real options → all three as buttons (4 rows)", kb4 and any(b.get("callback_data") == "ans:beta:2" for row in kb4 for b in row) and any(b.get("callback_data") == "ans:beta:3" for row in kb4 for b in row) and len(kb4) == 4 and kb4[-1][0]["text"].startswith("Apri ") and kb4[-1][0]["text"].endswith(" beta") and kb4[-1][0]["callback_data"] == "card:beta", str(kb4))
    T.check("A4c ❓ name / the question WHOLE / «n label · description» one whole line per option (the text says what the buttons cannot)", len(t[0].splitlines()) >= 3 and t[0].splitlines()[1] == "colore preferito?" and t[0].splitlines()[2] == "1 rosso · descrizione 1" and t[0].splitlines()[4] == "3 verde · descrizione 3", t[0])
    bs = json.loads((tmp / "state" / "bot-state.json").read_text()) if (tmp / "state" / "bot-state.json").is_file() else {}
    T.check("A4b bot state: both chats in the card of beta with the notice's message id", bs.get("chats", {}).get("1001", {}).get("session") == "beta" and bs["chats"]["1001"].get("level") == "card" and bs["chats"]["1001"].get("qmsg") == 1 and bs.get("chats", {}).get("1002", {}).get("session") == "beta", str(bs))
    led = (tmp / "state" / "ledger.jsonl")
    T.check("A4 ledger row ask-notified", led.is_file() and '"ask-notified"' in led.read_text() and '"beta"' in led.read_text(), led.read_text() if led.is_file() else "-")
    scr = subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-t", "beta"], capture_output=True, text=True).stdout
    T.check("A4 no key sent: the question is still open", "Enter to select" in scr and "❯ 1. rosso" in scr, scr)
# A7 (Franz 12/09 10:54: la domanda della master arrivava tagliata, non si poteva rispondere): la domanda va mostrata
# COMPLETA e di senso compiuto — l'ultima frase interrogativa se sta in synth_max_chars, altrimenti sintesi col
# modello (synth_model, `claude -p`), altrimenti il testo a capo. Il parser dello schermo unisce le righe della domanda.
import importlib.util as _ilu
os.environ.update({"CLAUDE_MASTER_CONFIG": str(cfg), "HOME": str(home)})
_spec = _ilu.spec_from_file_location("cm_answer", T.SCRIPTS / "cm-answer.py"); ans = _ilu.module_from_spec(_spec); _spec.loader.exec_module(ans)
scr7 = " ☐ Trasporto\n\n│ Il PC (Crostini) non accetta connessioni in entrata. Da dove possono passare\n│ stato e comandi fra PC e orologio?\n\n❯ 1. Firebase RTDB + FCM (Recommended)\n     Zero infrastruttura, sveglia push vera.\n  2. Relay sul tuo hosting + FCM\n  3. Type something.\n\nEnter to select · ↑/↓ to navigate · Esc to cancel\n"
d7 = ans.parse(scr7)
T.check("A7 parse joins the question lines (box chars stripped), header, options and their descriptions; footers dropped", d7 and d7[0] == "Trasporto" and d7[1] == "Il PC (Crostini) non accetta connessioni in entrata. Da dove possono passare stato e comandi fra PC e orologio?" and [o[1] for o in d7[2]] == ["Firebase RTDB + FCM (Recommended)", "Relay sul tuo hosting + FCM"] and d7[2][0][3] == "Zero infrastruttura, sveglia push vera." and d7[2][1][3] == "", str(d7))
scr6 = scr7.replace("  3. Type something.\n", "  3. Type something.\n────────\n  4. Chat about this\n")
d6 = ans.parse(scr6)
T.check("A6b (via master 12/09) «Type something.» and «Chat about this» and anything after them are footers, not options", d6 and [o[0] for o in d6[2]] == [1, 2], str(d6))
long_q = "Il PC (Crostini) non accetta connessioni in entrata. Da dove possono passare stato e comandi fra PC e orologio?"
CALLS["sendMessage"].clear()
r = notify(None, {"session_id": "sid-g", "cwd": str(home / "gamma"), "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": long_q, "header": "Trasporto",
                                                "options": [{"label": "Firebase RTDB + FCM (Recommended)"}, {"label": "Relay sul tuo hosting + FCM"}, {"label": "Telefono come ponte"}, {"label": "Tailscale diretto"}]}]}})
t7 = texts()[0].splitlines() if texts() else []
T.check("A7 the notice shows the interrogative sentence WHOLE on one line (no «…»), the context sentence dropped; first line «❓ … gamma · Trasporto»", r.returncode == 0 and t7 and t7[0].endswith(" gamma · Trasporto") and t7[1] == "Da dove possono passare stato e comandi fra PC e orologio?" and "…" not in texts()[0] and "Crostini" not in texts()[0], texts()[0] if texts() else r.stderr)
T.check("A7 options after it, one per line, «(Recommended)» dropped from the labels (no tmux here → no option buttons, as A5)", any(l == "1 Firebase RTDB + FCM" for l in t7) and t7[-1] == "4 Tailscale diretto", texts()[0])
fake_syn = tmp / "fake-synth.sh"; syn_log = tmp / "synth-args.log"
fake_syn.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(syn_log) + '"\necho "Dove passano stato e comandi fra PC e orologio?"\n'); fake_syn.chmod(0o755)
very_long = "Considerando che il PC in Crostini non accetta connessioni in entrata e che l'orologio Wear OS non ha un client Tailscale, quale canale preferisci per far passare stato e comandi fra il PC e l'orologio tenendo conto dei costi e della manutenzione"
CALLS["sendMessage"].clear()
r = notify(None, {"session_id": "sid-g", "cwd": str(home / "gamma"), "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": very_long, "header": "Trasporto", "options": [{"label": "A"}, {"label": "B"}]}]}}, CM_CLAUDE_BIN=str(fake_syn))
t7b = texts()[0].splitlines() if texts() else []
T.check("A7 a question too long for 88 chars and without a short interrogative sentence → synthesized by the model (claude -p, haiku, once), shown whole", r.returncode == 0 and syn_log.is_file() and syn_log.read_text().count("-p") == 1 and "--model haiku" in syn_log.read_text() and t7b[1] == "Dove passano stato e comandi fra PC e orologio?", texts()[0] + (syn_log.read_text() if syn_log.is_file() else "no call"))
cfg7 = json.loads(cfg.read_text()); cfg7["hooks"]["ask_notify"]["synth_model"] = ""; cfg.write_text(json.dumps(cfg7))
CALLS["sendMessage"].clear()
r = notify(None, {"session_id": "sid-g", "cwd": str(home / "gamma"), "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": very_long, "header": "Trasporto", "options": [{"label": "A"}, {"label": "B"}]}]}}, CM_CLAUDE_BIN=str(fake_syn))
t7c = texts()[0].splitlines() if texts() else []
T.check("A7 without a model (synth_model empty): the question WHOLE on one line (no cut), no model call; options listed (no tmux → no buttons)", r.returncode == 0 and syn_log.read_text().count("-p") == 1 and t7c[1] == very_long and t7c[2] == "1 A" and "…" not in texts()[0], texts()[0])
# A8 (Franz 12/09 11:12): il testo dice solo quello che i tasti non dicono — con tmux (tasti) e ≤ 3 opzioni senza
# descrizione: solo la domanda; con descrizioni dal payload: «n etichetta» + descrizione su ≤ 2 righe
with T.PrivateTmux() as tm8:
    subprocess.run(["tmux", "-L", tm8.socket, "new-session", "-d", "-s", "delta", "-x", "120", "-y", "40",
                    "env", "FAKE_CLAUDE_SCENARIO=question3", "FAKE_CLAUDE_REGISTER=0", str(FAKE)], env=env(tm8), check=True)
    time.sleep(2)
    pane8 = tm8("display-message", "-p", "-t", "delta", "#{pane_id}").stdout.strip()
    CALLS["sendMessage"].clear()
    r = notify(tm8, {"session_id": "sid-d", "cwd": str(home / "delta"), "tool_name": "AskUserQuestion",
                     "tool_input": {"questions": [{"question": "Procedo?", "header": "Via", "options": [{"label": "Sì"}, {"label": "No"}]}]}}, TMUX_PANE=pane8)
    t8 = texts()[0].splitlines() if texts() else []
    kb8 = json.loads(CALLS["sendMessage"][0]["reply_markup"])["inline_keyboard"] if CALLS["sendMessage"] else []
    T.check("A8 two options without description, buttons available → the question only, no option lines; buttons 1 Sì / 2 No / Apri", r.returncode == 0 and t8[1] == "Procedo?" and not any(l.startswith("1 ") or l.startswith("2 ") for l in t8) and [row[0]["text"] for row in kb8][:2] == ["1 Sì", "2 No"], texts()[0] + str(kb8))
    CALLS["sendMessage"].clear()
    r = notify(tm8, {"session_id": "sid-d", "cwd": str(home / "delta"), "tool_name": "AskUserQuestion",
                     "tool_input": {"questions": [{"question": "Procedo?", "header": "Via", "options": [{"label": "Sì", "description": "Parte subito e fa il commit alla fine del lavoro"}, {"label": "No", "description": "Resta fermo"}]}]}}, TMUX_PANE=pane8)
    t8b = texts()[0].splitlines() if texts() else []
    CALLS["sendMessage"].clear()
    ctx_q = "Il PC (Crostini) non accetta connessioni in entrata e l'orologio non ha un client Tailscale. Procedo con Firebase?"
    r = notify(tm8, {"session_id": "sid-d", "cwd": str(home / "delta"), "tool_name": "AskUserQuestion",
                     "tool_input": {"questions": [{"question": ctx_q, "header": "Via", "options": [{"label": "Sì"}, {"label": "No"}]}]}}, TMUX_PANE=pane8)
    t9 = texts()[0].splitlines() if texts() else []
    kb9 = json.loads(CALLS["sendMessage"][0]["reply_markup"])["inline_keyboard"] if CALLS["sendMessage"] else []
    bs9 = json.loads((tmp / "state" / "bot-state.json").read_text())
    T.check("A9 (via master 12/09 11:38) the synthesis dropped the context sentence → a «Domanda intera» button (q:delta) after the options, and the whole question kept in the bot state", r.returncode == 0 and t9[1] == "Procedo con Firebase?" and [row[0]["text"] for row in kb9] == ["1 Sì", "2 No", "Domanda intera", "Apri 🔴 delta"] and kb9[2][0]["callback_data"] == "q:delta" and bs9["chats"]["1001"]["qfull"]["delta"] == ctx_q, texts()[0] + str(kb9) + str(bs9.get("chats", {}).get("1001", {}).get("qfull")))
    T.check("A9 with a question shown whole (A8 before): no «Domanda intera» button", "Domanda intera" not in str(kb8), str(kb8))
    T.check("A8 descriptions from the payload → «1 Sì · description» and «2 No · Resta fermo», one whole line each", r.returncode == 0 and t8b[1] == "Procedo?" and t8b[2] == "1 Sì · Parte subito e fa il commit alla fine del lavoro" and t8b[3] == "2 No · Resta fermo", texts()[0])
cfg7["hooks"]["ask_notify"].pop("synth_model", None); cfg.write_text(json.dumps(cfg7))
# A5: senza tmux → dal payload
CALLS["sendMessage"].clear()
r = notify(None, {"session_id": "sid-x", "cwd": str(home / "ws" / "gamma"), "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": "quale taglia?", "header": "Taglia",
                                                "options": [{"label": "S", "description": "piccola"}, {"label": "M"}]}]}})
t = texts()
T.check("A5 no tmux: question and options from the payload, folder name, no «rispondi»", r.returncode == 0 and len(t) == 2 and "gamma" in t[0] and "quale taglia?" in t[0] and "1 S" in t[0] and "2 M" in t[0] and "rispondi" not in t[0], r.stdout + r.stderr + (t[0] if t else "-"))
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

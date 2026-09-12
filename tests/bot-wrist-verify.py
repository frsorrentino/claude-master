#!/usr/bin/env python3
"""Verifica il bot a misura di smartwatch (cm-bot.py + cm-bot-ui.py) con Telegram finto, dispatcher finto,
ledger e recap finti, un repo git finto per il checkpoint (mandato di Franz 11/09 16:36-16:40).

W1  «sessioni» (parola nuda) → elenco: righe ≤ 22, una per sessione, ❓ prima; silenzioso (disable_notification);
    tastiera coi numeri + riga fissa Elenco·Quota·Master; stato della chat = elenco
W2  «2» → scheda della seconda riga (≤ 8 righe ≤ 22), tastiera con opzioni + 0/f/t + riga fissa; stato = scheda
W3  in scheda con domanda: «due si» → `answer NOME 2`, risposta come reply al messaggio della scheda;
    prima un checkpoint git del workspace (stash create) registrato nello stato; «annulla» lo ripristina
W4  «f» → segue; al poll seguente uno stop nel ledger dopo un turno > 2 min → «✓ nome: esito» con notifica
    NORMALE; la sessione seguita sparisce → «✗ nome» e non più seguita
W5  «lancia alfa» → launch; «q» → quota compatta; «?» → aiuto; tap «list» → elenco; ogni risposta porta
    la riga fissa; «sessioni full» → tabella intera
W6  scadenza: stato scheda più vecchio di 10 min → un numero apre una scheda invece di rispondere
W9  PROMPT LIBERO dalla scheda (Franz 18:38): testo non-comando e non-risposta → anteprima «A <icona> nome:» + testo
    con bottoni Invia/Annulla (uno per riga); Invia → checkpoint git, `talk NOME "testo" --no-wait`, sessione
    seguita in automatico, «inviato a …» silenzioso; Annulla → niente; dall'elenco → «prima scegli la sessione»
    coi bottoni; testo < 3 caratteri ignorato (nessuna risposta); sessione ✗ → «non è viva», niente inviato
W10 RISPOSTA A UN PROMPT DEL POLSO (Franz 19:20): dopo Invia (o Riprendi) la sessione è «in attesa risposta»; al
    primo Stop la risposta torna al polso a prescindere dalla durata del turno, pulita, ≤ 8 righe, notifica
    NORMALE, con bottone «Tutto» se è più lunga (il tap manda il testo intero); il prompt consegnato porta il
    prefisso «Da Franz via Telegram (polso)…» che vieta la reply peer; Segui: soglia bot.follow_min_turn_s (30 s)
W7  `bot digest` → ❓ pendenti, ✓ ferme da ieri, ✗, un tasto «Apri» per ogni ❓; `bot install` scrive anche il cron
    delle 8:00 e chiama setMyCommands
W11 PARITÀ COL DESKTOP (12/09/2026 09:32): un prompt dal watch → UN messaggio vivo «📤 nome» + eco, con Ferma /
    Terminale / Sessioni, editato (editMessageText) dal transcript: «⚠ non ha ricevuto» + Invia di nuovo dopo
    receive_timeout_s senza il prompt nel transcript (il tap rimanda con --force); «▶ nome al lavoro» + il tool in
    corso + l'ultimo testo appena arriva; «▶ nome · 2m» col tempo; «❓ nome aspetta te» su domanda; un solo
    «⏳ ancora al lavoro» dopo answer_timeout_s; Ferma → Esc in tmux, «⏹ nome interrotta»; StopFailure → «✗ nome
    errore». La RISPOSTA e' lo Stop con la riga «Watch:» (o il primo se la sessione era ferma all'invio, o il
    secondo se era occupata): «✓ nome» + la riga, «Leggi tutto» per il resto, il messaggio vivo chiuso «✓ nome · Nm».
    Tasti nuovi ovunque (Sessioni, ◀ nome, Avvisami/Basta avvisi, Continua solo su ✓/✗, Annulla modifiche solo
    con checkpoint, Leggi tutto); «📍 nome» in testa a quota e aiuto; la scheda non scade mentre si aspetta la
    risposta; un testo fuori scheda va alla sola sessione in attesa.
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
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": ["1001"]}))
ws = home / "ws"
for d in ("personali/alfa/docs", "personali/api/docs", "personali/beta"):
    (ws / d).mkdir(parents=True)
state = tmp / "state"; state.mkdir()
API, CALLS, QUEUE = T.fake_telegram()
TMUX = T.PrivateTmux().__enter__()
# repo git finto per il checkpoint
repo = ws / "personali" / "api"
subprocess.run(["git", "init", "-q", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base"], check=True)
(repo / "file.txt").write_text("v1\n")
subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "v1"], check=True)
(repo / "file.txt").write_text("v2 lavoro in corso\n")
(repo / "docs" / "recap.md").write_text("# Recap di api\n\n- 2026-09-10: Deploy pronto sul server di prova · prossimo: Attendere la conferma del cliente\n")
# ledger finto
now = time.time()
def ts(t): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
ledger = state / "ledger.jsonl"
ledger.write_text("\n".join(json.dumps(r) for r in [
    {"ts": ts(now - 3 * 3600), "event": "start", "session_id": "S-REP", "cwd": str(repo), "account": "personale", "pid": 1},
    {"ts": ts(now - 2 * 3600), "event": "stop", "session_id": "S-REP", "cwd": str(repo), "account": "personale", "pid": 1, "last": "Deploy pronto sul server di prova, manca la conferma"},
    {"ts": ts(now - 2 * 86400), "event": "stop", "session_id": "S-ALFA", "cwd": str(ws / "personali" / "alfa"), "account": "personale", "pid": 2, "last": "Finito ieri l'altro"},
]) + "\n")
# dispatcher finto
argslog = tmp / "cm-args.log"
alive = tmp / "alive.json"
show = tmp / "show.txt"
fake_cm = tmp / "claude-master"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  launch) echo "sessione avviata"; echo "  link:      https://claude.ai/code/session_01BOT" ;;
  sessions) if [ "$2" = "--json" ]; then cat "{alive}"; else echo "PID ACCOUNT NOME STATO CARTELLA VISTA CANALE ATTIVA-DA"; echo "1 personale master idle ws:. aperta nativo 3h"; fi ;;
  answer) if [ "$3" = "--show" ]; then cat "{show}"; else echo "«$2»: risposto $3. B  (Vuoi A oppure B?)"; fi ;;
  quota) echo '{{"personale": {{"cinque_ore_pct": 23.4, "settimana_pct": 61}}, "professionale": {{"cinque_ore_pct": null, "settimana_pct": 80.2}}}}' ;;
  screen) echo "riga 1"; echo "riga 2 dello schermo" ;;
  registry) echo '{{"sessioni": [{{"nome": "beta", "cartella": "{ws / 'personali' / 'beta'}", "account": "personale", "visto": "2026-09-11T09:00:00"}}]}}' ;;
  talk) echo "consegnato" ;;
esac
""")
fake_cm.chmod(0o755)
show.write_text("«api» chiede — Deploy: Vuoi A oppure B?\n  ❯ 1. A\n    2. B\n    3. Type something.\n")
cron = tmp / "crontab"; cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state),
    "workspace": {"root": str(ws), "excluded_dirs": [".git"], "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}, "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "bot": {"enabled": True, "api_base": API, "token_file": str(tg / ".env"), "access_file": str(tg / "access.json"), "pid_file": str(tg / "bot.pid"), "cron_minutes": 1,
            "live_edit_s": 100, "receive_timeout_s": 15, "answer_timeout_s": 1000},
}))


def rows_alive(*names, status=None):
    base = {"master": {"pid": 7, "name": "master", "tmux": "master", "cwd": str(ws), "status": "busy", "waiting": False, "link": "https://claude.ai/code/session_01M", "account": "personale", "session_id": "S-M"},
            "api": {"pid": 8, "name": "api", "tmux": "api", "cwd": str(repo), "status": "waiting", "waiting": True, "link": "https://claude.ai/code/session_01R", "account": "personale", "session_id": "S-REP"},
            "alfa": {"pid": 9, "name": "alfa", "tmux": "alfa", "cwd": str(ws / "personali" / "alfa"), "status": "idle", "waiting": False, "link": "https://claude.ai/code/session_01A", "account": "personale", "session_id": "S-ALFA"},
            "master-2": {"pid": 10, "name": "master-2", "tmux": "master-2", "cwd": str(ws), "status": "busy", "waiting": False, "link": "https://claude.ai/code/session_01M2", "account": "personale", "session_id": "S-M2"}}
    rows = [dict(base[n]) for n in names]
    for r in rows:
        if status and r["tmux"] in status:
            r["status"] = status[r["tmux"]]; r["waiting"] = status[r["tmux"]] == "waiting"
    alive.write_text(json.dumps(rows))


rows_alive("master", "api", "alfa")


def bot(*args):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_BOT_CM": str(fake_cm), "CM_CRONTAB_CMD": str(fake_crontab), **TMUX.env}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-bot.py"), *args], capture_output=True, text=True, env=env, timeout=60)


uid = [100]


def say(text, chat=1001):
    uid[0] += 1
    QUEUE[:] = [{"update_id": uid[0], "message": {"message_id": uid[0], "chat": {"id": chat, "type": "private"}, "from": {"id": chat}, "text": text}}]
    CALLS["sendMessage"].clear()
    r = bot("poll")
    return r, CALLS["sendMessage"]


def tap(data, chat=1001):
    uid[0] += 1
    QUEUE[:] = [{"update_id": uid[0], "callback_query": {"id": f"cb{uid[0]}", "data": data, "from": {"id": chat}, "message": {"message_id": 55, "chat": {"id": chat, "type": "private"}}}}]
    CALLS["sendMessage"].clear()
    r = bot("poll")
    return r, CALLS["sendMessage"]


def kb(m):
    return json.loads(m.get("reply_markup") or '{"inline_keyboard": []}')["inline_keyboard"]


def has_fixed(m):
    """l'ultimo tasto e' «Sessioni» (tasti contestuali, Franz 21:00; nomi del 12/09), uno per riga"""
    return kb(m) and kb(m)[-1][0]["text"] == "Sessioni" and all(len(row) == 1 for row in kb(m))


def edits():
    return CALLS.get("editMessageText") or []


def live_of(name):
    return ((st()["chats"]["1001"].get("live") or {}).get(name)) or {}


def patch_live(name, **kw):
    s_ = st(); s_["chats"]["1001"]["live"][name].update(kw); (state / "bot-state.json").write_text(json.dumps(s_))


def patch_chat(**kw):
    s_ = st(); s_["chats"]["1001"].update(kw); (state / "bot-state.json").write_text(json.dumps(s_))


ICONS = set("🔴🟠🟡🟢🔵🟣⚪🟥🟧🟨🟩🟦🟪⬜")


def icon_in(text):
    return any(ch in ICONS for ch in text)


def cm_calls():
    return argslog.read_text().splitlines() if argslog.exists() else []


def st():
    p = state / "bot-state.json"
    return json.loads(p.read_text()) if p.is_file() else {}


(state / "bot-offset").write_text("100")
# W1
r, sent = say("Sessioni!")
m = sent[-1] if sent else {}
lines = m.get("text", "").splitlines()
T.check("W1 «sessioni» → ONE header line «4 sessioni · 1❓ · 1✗», no text rows", r.returncode == 0 and len(lines) == 1 and lines[0] == "4 sessioni · 1❓ · 1✗", r.stdout + r.stderr + str(sent))
T.check("W1 silent, one name button per row (4) with state + icon, ordered ❓▶✓✗, then Recap/Quota (master alive: no Avvia master); chat state = list", m.get("disable_notification") == "true" and [row[0]["text"][0] for row in kb(m)[:4]] == ["❓", "▶", "✓", "✗"] and all(icon_in(row[0]["text"]) for row in kb(m)[:4]) and kb(m)[1][0]["text"].endswith(" master") and kb(m)[1][0]["callback_data"] == "n:2" and [row[0]["text"] for row in kb(m)[-2:]] == ["Recap", "Quota"] and st()["chats"]["1001"]["level"] == "list", str(kb(m)) + str(st()))
# W2
r, sent = say("2")
m = sent[-1] if sent else {}
lines = m.get("text", "").splitlines()
T.check("W2 «2» → card of master (second row): ≤ 20 lines ≤ 22, «▶ 🔴 master» (state icon first), «lavora …», «→ nessun recap»", lines and lines[0].endswith(" master") and lines[0].startswith("▶ ") and icon_in(lines[0]) and "pers" not in lines[0] and lines[1].startswith("lavora") and "→ nessun recap" in lines and len(lines) <= 20 and all(len(l) <= 22 for l in lines), str(lines))
T.check("W2 card keyboard of a ▶ session: Avvisami / Sessioni (no Continua while it works, no Annulla modifiche without a checkpoint), one per row; state = card master", [row[0]["text"] for row in kb(m)] == ["Avvisami", "Sessioni"] and all(len(row) == 1 for row in kb(m)) and st()["chats"]["1001"]["level"] == "card" and st()["chats"]["1001"]["session"] == "master", str(kb(m)) + str(st()))
r, sent = say("1")
m = sent[-1] if sent else {}
lines = m.get("text", "").splitlines()
T.check("W2 «1» from a card goes back through the list: card of api — «api · personale», «❓ aspetta te», clean esito, «→ prossimo», question, options 1-3", lines and lines[0].endswith(" api") and lines[0].startswith("❓ ") and icon_in(lines[0]) and lines[1] == "aspetta te" and any(l.startswith("1 A") for l in lines) and any(l.startswith("3 Type") for l in lines) and any(l.startswith("→ Attendere") for l in lines) and any("Deploy" in l for l in lines) and len(lines) <= 20, str(lines))
qmsg_before = st()["chats"]["1001"].get("qmsg")
T.check("W2 option buttons with their text, one per row; the card's message id remembered for the reply", [row[0]["text"] for row in kb(m)[:3]] == ["1 A", "2 B", "3 Type something."] and kb(m)[1][0]["callback_data"] == "opt:2" and qmsg_before is not None, str(kb(m)) + str(st()))
# W3
r, sent = say("due si")
m = sent[-1] if sent else {}
T.check("W3 «due si» in a card with a question → answer api 2, reply to the card message, «📤 api» + echo of the choice, live keyboard Ferma/Terminale/Sessioni, api awaiting + live (kind answer)", r.returncode == 0 and "answer api 2" in cm_calls()[-2:] and m.get("reply_to_message_id") == str(qmsg_before) and "risposto 2" in m.get("text", "") and m["text"].startswith("📤 ") and [row[0]["text"] for row in kb(m)] == ["Ferma", "Terminale", "Sessioni"] and "api" in st()["chats"]["1001"]["awaiting"] and live_of("api").get("kind") == "answer" and live_of("api").get("mid"), r.stdout + r.stderr + str(cm_calls()[-2:]) + str(m) + str(st()))
ck = st()["chats"]["1001"].get("checkpoints", {}).get("api")
T.check("W3 a git checkpoint of the workspace taken before answering (stash create sha stored)", ck and len(ck.get("sha", "")) == 40 and ck.get("cwd") == str(repo), str(ck))
(repo / "file.txt").write_text("v3 rovinato\n")
r, sent = say("annulla")
T.check("W3 «annulla» restores the tracked files to the checkpoint: «modifiche annullate», «◀ api» + Sessioni under it", r.returncode == 0 and (repo / "file.txt").read_text() == "v2 lavoro in corso\n" and "annullate" in sent[-1]["text"] and kb(sent[-1])[0][0]["text"].endswith(" api") and kb(sent[-1])[0][0]["callback_data"] == "card:api" and has_fixed(sent[-1]), r.stdout + r.stderr + str(sent) + (repo / "file.txt").read_text())
r, sent = tap("card:api")
T.check("W3 api's card now offers «Annulla modifiche» (a checkpoint exists) and «Basta avvisi» (followed since the answer), no Continua (❓)", [row[0]["text"] for row in kb(sent[-1])[-3:]] == ["Basta avvisi", "Annulla modifiche", "Sessioni"] and "Continua" not in [row[0]["text"] for row in kb(sent[-1])], str(kb(sent[-1])))
# W4
r, sent = say("f")
T.check("W4 «f» on a session already followed (since the answer) → «basta avvisi», silent, «◀ api» under it", "api" not in st()["chats"]["1001"]["follow"] and sent and "basta avvisi" in sent[-1]["text"] and sent[-1].get("disable_notification") == "true" and kb(sent[-1])[0][0]["callback_data"] == "card:api", str(st()) + str(sent))
r, sent = say("avvisami")
T.check("W4 «avvisami» follows api again (state), confirmation silent", "api" in st()["chats"]["1001"]["follow"] and sent and sent[-1].get("disable_notification") == "true", str(st()) + str(sent))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 200), "event": "stop", "session_id": "S-REP", "cwd": str(repo), "account": "personale", "pid": 1, "last": "Pubblicato in produzione, tutto verde"}) + "\n")
CALLS["sendMessage"].clear(); QUEUE[:] = []
r = bot("poll")
al = [m for m in CALLS["sendMessage"] if m["text"].startswith("✓ ") and "api" in m["text"].splitlines()[0]]
T.check("W4 next poll: the first Stop after the answer to the question (api was ❓, not busy) is the ANSWER: «✓ api risponde:» NORMAL, Apri/Basta avvisi, live message closed «✓ … api · Nm», no longer awaiting", r.returncode == 0 and al and al[0].get("disable_notification") != "true" and "Pubblicato" in al[0]["text"] and al[0]["text"].splitlines()[0].endswith("risponde:") and [row[0]["text"] for row in kb(al[0])][-1] == "Basta avvisi" and edits() and edits()[-1]["text"].startswith("✓ ") and "api" in edits()[-1]["text"] and "api" not in st()["chats"]["1001"]["awaiting"] and not live_of("api"), r.stdout + r.stderr + str(CALLS["sendMessage"]) + str(edits()[-1:]))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 300), "event": "stop", "session_id": "S-REP", "cwd": str(repo), "account": "personale", "pid": 1, "last": "Anche il secondo deploy fatto", "esito": "Esito: secondo deploy fatto in /srv/www. Restano i log da pulire", "tail": "- Anche il secondo deploy fatto\nEsito: secondo deploy fatto in /srv/www. Restano i log da pulire"}) + "\n")
CALLS["sendMessage"].clear(); r = bot("poll")
al = [m for m in CALLS["sendMessage"] if m["text"].startswith("✓ ") and "api" in m["text"].splitlines()[0]]
T.check("W4 a later Stop of the followed session, turn > follow_min_turn_s → «✓ api ha finito:» NORMAL; the «Esito:» line is ONE sentence for voice reading (path shortened), the rest after it", al and al[0].get("disable_notification") != "true" and al[0]["text"].splitlines()[0].endswith("ha finito:") and al[0]["text"].splitlines()[1] == "Esito: secondo deploy fatto in www." and al[0]["text"].splitlines()[2] == "Restano i log da pulire", str(CALLS["sendMessage"]))
CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W4 the same stop is not announced twice", not [m for m in CALLS["sendMessage"] if m["text"].startswith("✓ ") and "api" in m["text"]], str(CALLS["sendMessage"]))
rows_alive("master", "alfa")
CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W4 the followed session disappears → «✗ api», normal notification, no longer followed", any(m["text"].startswith("✗ ") and "api" in m["text"] and m.get("disable_notification") != "true" for m in CALLS["sendMessage"]) and "api" not in st()["chats"]["1001"]["follow"], str(CALLS["sendMessage"]) + str(st()))
rows_alive("master", "api", "alfa")
# W5
r, sent = say("lancia beta")
T.check("W5 «lancia beta» → launch, back button on the reply", cm_calls()[-1].startswith("launch ") and "beta" in cm_calls()[-1] and has_fixed(sent[-1]), str(cm_calls()[-1:]) + str(sent))
r, sent = say("q")
T.check("W5 «q» → «📍 api» first (the card you are in), compact quota (5h and week), ≤ 22 per line, «◀ api» + Sessioni", sent and "personale 23% 61%" in sent[-1]["text"] and "profession - 80%" in sent[-1]["text"] and sent[-1]["text"].startswith("📍 ") and sent[-1]["text"].splitlines()[1].startswith("5h") and kb(sent[-1])[0][0]["callback_data"] == "card:api" and all(len(l) <= 22 for l in sent[-1]["text"].splitlines()) and has_fixed(sent[-1]), str(sent))
r, sent = tap("recap")
T.check("W5 tap «Recap» → an immediate silent «⏳» then the recap with a NORMAL notification and the fixed rows", len(sent) >= 2 and sent[-2]["text"].startswith("⏳") and sent[-2].get("disable_notification") == "true" and sent[-1]["text"].startswith("Recap") and sent[-1].get("disable_notification") != "true" and has_fixed(sent[-1]), str(sent)[:400])
r, sent = say("recap")
T.check("W5 dictated «recap» → the same two messages", len(sent) >= 2 and sent[-2]["text"].startswith("⏳") and sent[-1]["text"].startswith("Recap"), str(sent)[:300])
r, sent = say("?")
T.check("W5 «?» → «📍 api» + compact help with the bare words (avvisami, continua, ferma, terminale)", sent and "sessioni" in sent[-1]["text"] and sent[-1]["text"].startswith("📍 ") and "avvisami" in sent[-1]["text"] and "ferma" in sent[-1]["text"] and all(len(l) <= 22 for l in sent[-1]["text"].splitlines()) and has_fixed(sent[-1]), str(sent))
r, sent = tap("list")
T.check("W5 tap «list» → the list again", sent and sent[-1]["text"].startswith("4 sessioni") and st()["chats"]["1001"]["level"] == "list", str(sent))
r, sent = tap("n:2")
T.check("W5 tap on the «▶ master» name button opens its card", sent and sent[-1]["text"].splitlines()[0].endswith(" master") and st()["chats"]["1001"]["session"] == "master", str(sent))
r, sent = say("sessioni full")
T.check("W5 «sessioni full» → the whole table", sent and "PID ACCOUNT" in sent[-1]["text"], str(sent))
# W8 (18:28): il nome dettato apre la scheda; un prefisso ambiguo → i soli bottoni delle candidate
rows_alive("master", "api", "alfa", "master-2")
r, sent = say("api")
T.check("W8 dictated «api» → its card", sent and sent[-1]["text"].splitlines()[0].endswith(" api") and st()["chats"]["1001"]["session"] == "api", str(sent))
r, sent = say("al")
T.check("W8 unique prefix «al» → alfa's card", sent and sent[-1]["text"].splitlines()[0].endswith(" alfa"), str(sent))
r, sent = say("mas")
T.check("W8 ambiguous «mas» → «2 sessioni: quale?» with only the two candidate buttons + fixed rows", sent and sent[-1]["text"] == "2 sessioni: quale?" and [row[0]["text"].split()[-1] for row in kb(sent[-1])[:2]] == ["master", "master-2"] and len(kb(sent[-1])) == 4, str(sent) + str(kb(sent[-1])))
r, sent = tap("n:2")
T.check("W8 tap on the second candidate → master-2's card", sent and sent[-1]["text"].splitlines()[0].endswith(" master-2"), str(sent))
rows_alive("master", "api", "alfa")
# W9: prompt libero dalla scheda (Franz 21:05: niente anteprima, la tastiera Wear OS conferma gia')
say("elenco"); say("mast")   # «master» e' un comando: il prefisso apre la scheda
CALLS["sendMessage"].clear()
r, sent = say("riassumi lo stato in due righe")
m9 = sent[-1] if sent else {}
T.check("W9 free text in a card → sent at once: talk master \"…\" --no-wait, master followed, live message «📤 … master» + «riassumi lo stato in…» silent, its message_id kept, busy_at_send (master ▶)", sent and any(c.startswith("talk master ") and c.endswith("riassumi lo stato in due righe --no-wait") for c in cm_calls()[-3:]) and "master" in st()["chats"]["1001"]["follow"] and m9.get("text", "").startswith("📤 ") and m9["text"].splitlines()[0].endswith(" master") and m9["text"].splitlines()[1] == "«riassumi lo stato in…" and m9.get("disable_notification") == "true" and live_of("master").get("mid") and live_of("master").get("busy_at_send") is True, "SENT=" + str(sent) + " CALLS=" + str(cm_calls()[-3:]) + " FOLLOW=" + str(st()["chats"]["1001"].get("follow")))
T.check("W9 live message buttons: Ferma / Terminale / Sessioni", [row[0]["text"] for row in kb(m9)] == ["Ferma", "Terminale", "Sessioni"] and kb(m9)[0][0]["callback_data"] == "stop:master", str(kb(m9)))
T.check("W9 the live message was edited at once (no transcript → assumed received): «▶ … master al lavoro»", edits() and edits()[-1]["text"].startswith("▶ ") and edits()[-1]["text"].endswith(" master al lavoro"), str(edits()[-1:]))
r, sent = tap("screen:master")
T.check("W9 tap «Terminale» → `screen master --lines 30`, the screen in a <pre> block (HTML), «◀ master» + Sessioni", sent and "riga 2 dello schermo" in sent[-1]["text"] and sent[-1]["text"].startswith("<pre>") and sent[-1].get("parse_mode") == "HTML" and "screen master --lines 30" in cm_calls()[-3:] and kb(sent[-1])[0][0]["callback_data"] == "card:master" and has_fixed(sent[-1]), str(sent) + str(cm_calls()[-1:]))
r, sent = say("v alfa")
T.check("W9 dictated «v alfa» → alfa's screen (alias of schermo/terminale)", sent and sent[-1]["text"].startswith("<pre>") and "screen alfa --lines 30" in cm_calls()[-3:], str(sent) + str(cm_calls()[-1:]))
T.check("W9 a git checkpoint of the session's folder was taken before sending (ws is not a repo → none; ledger-api's would be)", "checkpoints" in st()["chats"]["1001"], str(st()["chats"]["1001"].get("checkpoints")))
r, sent = tap("card:master")
T.check("W9 tap «◀ master» (card:) → master's card", sent and sent[-1]["text"].splitlines()[0].endswith(" master"), str(sent)[:200])
r, sent = say("ok")
T.check("W9 text under 3 characters: ignored, no reply", not sent, str(sent))
r, sent = say("elenco")
r, sent = say("riassumi lo stato in due righe")
T.check("W9 free text at list level with ONE session awaiting → it goes to that session («📤 … master»)", sent and sent[-1]["text"].startswith("📤 ") and sent[-1]["text"].splitlines()[0].endswith(" master") and cm_calls()[-2].startswith("talk master "), str(sent) + str(cm_calls()[-2:]))
patch_chat(awaiting={}, follow=[], live={}, level="list")
r, sent = say("riassumi lo stato in due righe")
T.check("W9 free text at list level with nothing pending → «prima scegli la sessione» with the session buttons, nothing sent", sent and "prima scegli" in sent[-1]["text"] and any(row[0]["text"].endswith(" master") for row in kb(sent[-1])) and not [c for c in cm_calls()[-1:] if c.startswith("talk ")], str(sent) + str(cm_calls()[-1:]))
say("beta")   # ✗ dalla fotografia
n_talk = len([c for c in cm_calls() if c.startswith("talk ")])
r, sent = say("fai qualcosa di utile")
T.check("W9 a ✗ session: «non è viva», nothing sent", sent and "viva" in sent[-1]["text"] and len([c for c in cm_calls() if c.startswith("talk ")]) == n_talk, str(sent) + str(cm_calls()[-1:]))
say("elenco")
# W10: risposta a un prompt del watch. master e' ▶ all'invio: il PRIMO Stop senza riga Watch e' il turno
# precedente (non annunciato: turno di 10 s), il secondo con «Watch:» e' la risposta
say("mast"); r, sent = say("riassumi lo stato in due righe")
T.check("W10 the delivered prompt carries the sender/channel prefix «(watch)», forbids peer replies and asks for the «Watch:» line", any(c.startswith("talk master Da Franz via Telegram (watch)") and "NON usare SendMessage" in c and "Watch:" in c and c.endswith("riassumi lo stato in due righe --no-wait") for c in cm_calls()[-3:]), str(cm_calls()[-3:]))
T.check("W10 master marked «awaiting», busy_at_send", "master" in (st()["chats"]["1001"].get("awaiting") or {}) and live_of("master").get("busy_at_send") is True, str(st()["chats"]["1001"].get("awaiting")))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 300), "event": "start", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7}) + "\n")
    f.write(json.dumps({"ts": ts(now + 310), "event": "stop", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "last": "turno precedente finito", "tail": "turno precedente finito", "esito": ""}) + "\n")
CALLS["sendMessage"].clear(); QUEUE[:] = []
r = bot("poll")
T.check("W10 the first Stop without «Watch:» while busy at send is the PREVIOUS turn: nothing sent (10 s turn), still awaiting, marked skipped", not [m for m in CALLS["sendMessage"] if "master" in m["text"]] and "master" in st()["chats"]["1001"]["awaiting"] and live_of("master").get("skipped") is True, str(CALLS["sendMessage"]) + str(live_of("master")))
long_text = "Esito: stato riassunto, due file toccati e test verdi. " + "Dettaglio " * 30 + "\nWatch: due file toccati, test verdi"
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 330), "event": "stop", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "last": long_text[:300], "tail": long_text[-600:], "esito": "Esito: stato riassunto, due file toccati e test verdi.", "watch": "Watch: due file toccati, test verdi"}) + "\n")
CALLS["sendMessage"].clear(); r = bot("poll")
ans = [m for m in CALLS["sendMessage"] if m["text"].startswith("✓ ") and "master" in m["text"].splitlines()[0]]
T.check("W10 the Stop with «Watch:» is the answer: «✓ … master» + the bare Watch line only, NORMAL notification, Leggi tutto / Apri … master / Basta avvisi", ans and ans[0].get("disable_notification") != "true" and ans[0]["text"].splitlines()[0].endswith(" master") and ans[0]["text"].splitlines()[1] == "due file toccati, test verdi" and len(ans[0]["text"].splitlines()) == 2 and [row[0]["text"] for row in kb(ans[0])] == ["Leggi tutto", "Apri 🔴 master", "Basta avvisi"], str(CALLS["sendMessage"]))
T.check("W10 no longer awaiting; live message closed «✓ … master · Nm» (age since the send), chat in master's card", "master" not in (st()["chats"]["1001"].get("awaiting") or {}) and not live_of("master") and edits()[-1]["text"].startswith("✓ ") and " master · " in edits()[-1]["text"] and edits()[-1]["text"].endswith("m") and st()["chats"]["1001"]["session"] == "master", str(st()["chats"]["1001"].get("awaiting")) + str(edits()[-1:]))
full_cb = next(b["callback_data"] for row in kb(ans[0]) for b in row if b["text"] == "Leggi tutto")
r, sent = tap(full_cb)
T.check("W10 tap «Leggi tutto» → the whole answer («Esito:» + the detail), «◀ master» + Sessioni", sent and sent[-1]["text"].startswith("Esito: stato riassunto") and "Dettaglio Dettaglio" in sent[-1]["text"] and kb(sent[-1])[0][0]["callback_data"] == "card:master" and has_fixed(sent[-1]), str(sent)[:300])
# master FERMA all'invio: il primo Stop e' la risposta anche senza riga Watch (il modello l'ha ignorata) → ripiego «risponde:»
rows_alive("master", "api", "alfa", status={"master": "idle"})
say("mast"); say("ancora una cosa da fare")
T.check("W10 idle at send: busy_at_send False", live_of("master").get("busy_at_send") is False, str(live_of("master")))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 350), "event": "start", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7}) + "\n")
    f.write(json.dumps({"ts": ts(now + 360), "event": "stop", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "last": "x", "tail": "Paragrafo lungo di lavoro fatto. " * 60, "esito": "Esito: molto lungo"}) + "\n")
CALLS["sendMessage"].clear(); QUEUE[:] = []; r = bot("poll")
ans2 = [m for m in CALLS["sendMessage"] if m["text"].startswith("✓ ") and "risponde" in m["text"].splitlines()[0]]
T.check("W10 no «Watch:» line, idle at send: the first Stop is the answer, «✓ … master risponde:», «Esito:» first, cut at ~1200 chars with «Leggi tutto»", ans2 and ans2[0]["text"].splitlines()[1] == "Esito: molto lungo" and len(ans2[0]["text"]) <= 1300 and kb(ans2[0])[0][0]["text"] == "Leggi tutto", "SENT=" + str(CALLS["sendMessage"])[:300] + " AW=" + str(st()["chats"]["1001"].get("awaiting")) + " SEEN=" + str(st()["chats"]["1001"].get("seen")) + " CALLS=" + str(cm_calls()[-4:]))
full_cb = next(b["callback_data"] for row in kb(ans2[0]) for b in row if b["text"] == "Leggi tutto")
r, sent = tap(full_cb)
T.check("W10 tap «Leggi tutto» → the whole text in a second message", sent and sent[-1]["text"].count("Paragrafo lungo") >= 50, str(sent)[:200])
rows_alive("master", "api", "alfa")
# W10b: soglia di Segui a 30 s: turno di 10 s non annunciato, di 40 s sì (master e' seguita dopo Invia)
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 400), "event": "start", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7}) + "\n")
    f.write(json.dumps({"ts": ts(now + 410), "event": "stop", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "last": "turno corto", "tail": "turno corto", "esito": ""}) + "\n")
CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W10b followed session, 10 s turn → nothing", not [m for m in CALLS["sendMessage"] if "master" in m["text"]], str(CALLS["sendMessage"]))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 500), "event": "start", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7}) + "\n")
    f.write(json.dumps({"ts": ts(now + 540), "event": "stop", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "last": "turno di 40 secondi", "tail": "turno di 40 secondi", "esito": ""}) + "\n")
CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W10b followed session, 40 s turn → «✓ … master» with the outcome", any(m["text"].startswith("✓ ") and "master" in m["text"] and "40 secondi" in m["text"] for m in CALLS["sendMessage"]), str(CALLS["sendMessage"]))
T.check("W10b after the outcome the chat sits in master's card: a dictated text goes to master", st()["chats"]["1001"]["level"] == "card" and st()["chats"]["1001"]["session"] == "master", str(st()["chats"]["1001"]))
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 600), "event": "start", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7}) + "\n")
    f.write(json.dumps({"ts": ts(now + 650), "event": "stop", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "last": "x", "tail": "Tre file toccati, test verdi, changelog aggiornato, README rigenerato con le card nuove, privacy verde, commit locale fatto e cache nei due account " * 2, "esito": "Esito: tutto verde, tre file toccati, changelog e README aggiornati"}) + "\n")
CALLS["sendMessage"].clear(); r = bot("poll")
o = [m for m in CALLS["sendMessage"] if m["text"].startswith("✓ ") and "master" in m["text"].splitlines()[0]]
T.check("W10b a long outcome: «ha finito:», «Esito:» first and whole, the tail readable, Apri / Basta avvisi", o and o[0]["text"].splitlines()[0].endswith("ha finito:") and o[0]["text"].splitlines()[1].startswith("Esito: tutto verde, tre file toccati") and "README rigenerato" in o[0]["text"] and kb(o[0])[0][0]["text"].startswith("Apri ") and kb(o[0])[1][0]["text"] == "Basta avvisi", str(o))
# W11: il messaggio vivo dal transcript (parita' col desktop)
slug = "".join(ch if ch.isalnum() else "-" for ch in os.path.realpath(str(ws)))
tdir = home / ".claude" / "projects" / slug; tdir.mkdir(parents=True, exist_ok=True)
transcript = tdir / "S-M.jsonl"; transcript.write_text("")
rows_alive("master", "api", "alfa", status={"master": "idle"})
say("mast"); r, sent = say("controlla i test della suite")
m11 = sent[-1] if sent else {}
T.check("W11 prompt with a transcript on disk: «📤 … master» + echo, live entry with the transcript path and offset, NOT yet received", m11.get("text", "").startswith("📤 ") and live_of("master").get("transcript") == str(transcript) and live_of("master").get("received") is False and live_of("master").get("mid"), str(m11) + str(live_of("master")))
n_e = len(edits()); CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W11 poll within receive_timeout_s, nothing in the transcript: no edit, no message", len(edits()) == n_e and not CALLS["sendMessage"], str(edits()[n_e:]) + str(CALLS["sendMessage"]))
patch_live("master", sent_ts=time.time() - 20)
CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W11 after receive_timeout_s without the prompt in the transcript: the live message becomes «⚠ … master» / «non ha ricevuto» with Invia di nuovo / Sessioni, edited (no new message)", len(edits()) == n_e + 1 and edits()[-1]["text"].splitlines()[0].startswith("⚠ ") and "non ha ricevuto" in edits()[-1]["text"] and [row[0]["text"] for row in json.loads(edits()[-1]["reply_markup"])["inline_keyboard"]] == ["Invia di nuovo", "Sessioni"] and edits()[-1]["message_id"] == str(live_of("master")["mid"]) and not CALLS["sendMessage"], str(edits()[-1:]) + str(CALLS["sendMessage"]))
r, sent = tap("retry:master")
T.check("W11 tap «Invia di nuovo» → the same prompt again with --force, a fresh «📤» live message", sent and sent[-1]["text"].startswith("📤 ") and any(c.startswith("talk master ") and "controlla i test della suite --no-wait --force" in c for c in cm_calls()[-3:]) and live_of("master").get("received") is False, str(sent) + str(cm_calls()[-3:]))
with open(transcript, "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Da Franz via Telegram (watch). … controlla i test della suite"}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Lancio la suite dei test."}, {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q tests", "description": "run tests"}}]}}) + "\n")
n_e = len(edits()); CALLS["sendMessage"].clear(); r = bot("poll")
T.check("W11 the prompt shows up in the transcript → received; the live message edited: «▶ … master al lavoro» + «Bash pytest -q tests» (the tool in progress), Ferma/Terminale/Sessioni, no new message", live_of("master").get("received") is True and len(edits()) == n_e + 1 and edits()[-1]["text"].splitlines()[0].endswith(" master al lavoro") and edits()[-1]["text"].splitlines()[1] == "Bash pytest -q tests" and [row[0]["text"] for row in json.loads(edits()[-1]["reply_markup"])["inline_keyboard"]] == ["Ferma", "Terminale", "Sessioni"] and not CALLS["sendMessage"], str(edits()[n_e:]) + str(live_of("master")))
with open(transcript, "a") as f:
    f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "3 passed"}]}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Tre test **verdi**, ora aggiorno il changelog e il README con le righe nuove."}]}}) + "\n")
n_e = len(edits()); r = bot("poll")
T.check("W11 a new text before live_edit_s (100 s here): throttled, no edit", len(edits()) == n_e, str(edits()[n_e:]))
patch_live("master", last_edit=time.time() - 200, sent_ts=time.time() - 130)
r = bot("poll")
T.check("W11 past the cadence: «▶ … master · 2m», the tool, the last text in ≤ 2 lines of ≤ 22 (markdown stripped), ≤ 4 lines", len(edits()) == n_e + 1 and edits()[-1]["text"].splitlines()[0].endswith(" master · 2m") and edits()[-1]["text"].splitlines()[1] == "Bash pytest -q tests" and edits()[-1]["text"].splitlines()[2].startswith("Tre test verdi") and len(edits()[-1]["text"].splitlines()) <= 4 and all(len(l) <= 22 for l in edits()[-1]["text"].splitlines()), str(edits()[-1:]))
rows_alive("master", "api", "alfa", status={"master": "waiting"})
patch_live("master", last_edit=time.time() - 200)
r = bot("poll")
T.check("W11 the session stops on a question → «❓ … master aspetta te»", edits()[-1]["text"].startswith("❓ ") and edits()[-1]["text"].endswith(" master aspetta te"), str(edits()[-1:]))
rows_alive("master", "api", "alfa", status={"master": "busy"})
patch_live("master", last_edit=time.time() - 200, sent_ts=time.time() - 1100)
n_s = len(CALLS["sendMessage"]); r = bot("poll")
T.check("W11 past answer_timeout_s: ONE silent «⏳ … master ancora al lavoro», not repeated", len(CALLS["sendMessage"]) == n_s + 1 and CALLS["sendMessage"][-1]["text"].startswith("⏳ ") and CALLS["sendMessage"][-1].get("disable_notification") == "true" and live_of("master").get("long_warned") is True, str(CALLS["sendMessage"][n_s:]))
n_s = len(CALLS["sendMessage"]); patch_live("master", last_edit=time.time() - 200); r = bot("poll")
T.check("W11 …not repeated", len(CALLS["sendMessage"]) == n_s, str(CALLS["sendMessage"][n_s:]))
# Ferma: Esc nel riquadro tmux della sessione (server privato)
TMUX("new-session", "-d", "-s", "master", "cat")
r, sent = tap("stop:master")
T.check("W11 tap «Ferma» with the tmux session alive → «⏹ … master interrotta», «◀ master» + Sessioni, no longer awaiting, live message closed «⏹»", sent and sent[-1]["text"].startswith("⏹ ") and "interrotta" in sent[-1]["text"] and kb(sent[-1])[0][0]["callback_data"] == "card:master" and "master" not in st()["chats"]["1001"]["awaiting"] and not live_of("master") and edits()[-1]["text"].startswith("⏹ "), str(sent) + str(edits()[-1:]) + str(st()["chats"]["1001"]))
TMUX("kill-session", "-t", "=master")
say("alfa"); r, sent = say("ferma")
T.check("W11 «ferma» dictated on a session without a tmux pane → «⚠ alfa» / «non si ferma»", sent and sent[-1]["text"].splitlines()[0].startswith("⚠ ") and "non si ferma" in sent[-1]["text"], str(sent))
# StopFailure di una sessione seguita/in attesa → «✗ nome errore»
say("mast"); say("prova ancora")
with open(ledger, "a") as f:
    f.write(json.dumps({"ts": ts(now + 800), "event": "stop-failure", "session_id": "S-M", "cwd": str(ws), "account": "personale", "pid": 7, "error": "rate limit reached\nretry later"}) + "\n")
CALLS["sendMessage"].clear(); QUEUE[:] = []; r = bot("poll")
T.check("W11 StopFailure while awaiting → «✗ … master errore» + the first error line, NORMAL notification, live closed, no longer awaiting", CALLS["sendMessage"] and CALLS["sendMessage"][-1]["text"].splitlines()[0].endswith(" master errore") and CALLS["sendMessage"][-1]["text"].splitlines()[1] == "rate limit reached" and CALLS["sendMessage"][-1].get("disable_notification") != "true" and "master" not in st()["chats"]["1001"]["awaiting"] and not live_of("master"), str(CALLS["sendMessage"]) + str(st()["chats"]["1001"]))
# la scheda NON scade mentre si aspetta la risposta
say("mast"); say("ultima cosa")
patch_chat(until=time.time() - 1)
r, sent = say("e poi questa")
T.check("W11 an expired card whose session is awaiting is still the card: the text goes to master", sent and sent[-1]["text"].startswith("📤 ") and sent[-1]["text"].splitlines()[0].endswith(" master"), str(sent))
patch_chat(awaiting={}, live={})
# Continua solo su ✓ ferma
r, sent = say("alfa")
T.check("W11 card of a ✓ session: Avvisami / Continua / Sessioni", [row[0]["text"] for row in kb(sent[-1])] == ["Avvisami", "Continua", "Sessioni"], str(kb(sent[-1])))
r, sent = say("continua")
T.check("W11 «continua» → talk alfa with the resume prompt, «📤 … alfa» + «continua → …», live keyboard, alfa awaiting", any(c.startswith("talk alfa Da Franz via Telegram (watch)") and "--no-wait" in c for c in cm_calls()[-3:]) and sent and sent[-1]["text"].startswith("📤 ") and "continua" in sent[-1]["text"] and [row[0]["text"] for row in kb(sent[-1])] == ["Ferma", "Terminale", "Sessioni"] and "alfa" in st()["chats"]["1001"]["awaiting"], str(sent) + str(cm_calls()[-3:]))
patch_chat(awaiting={}, live={}, follow=[])
say("elenco")
# W6
say("1")
s6 = st(); s6["chats"]["1001"]["until"] = time.time() - 1; (state / "bot-state.json").write_text(json.dumps(s6))
n_ans = len([c for c in cm_calls() if c.startswith("answer ") and not c.endswith("--show")])
r, sent = say("2")
T.check("W6 expired card: «2» opens a card instead of answering", sent and sent[-1]["text"].splitlines()[0].endswith(" master") and len([c for c in cm_calls() if c.startswith("answer ") and not c.endswith("--show")]) == n_ans, str(sent) + str(cm_calls()[-3:]))
# W7
r = bot("digest")
T.check("W7 digest: an «Apri … api» button for the ❓, then Sessioni", kb(CALLS["sendMessage"][-1])[0][0]["text"].startswith("Apri ") and kb(CALLS["sendMessage"][-1])[0][0]["callback_data"] == "card:api" and has_fixed(CALLS["sendMessage"][-1]), str(kb(CALLS["sendMessage"][-1])))
T.check("W7 digest: ❓ pending, ✓ idle since yesterday, ✗ from the snapshot; ≤ 22 per line", r.returncode == 0 and CALLS["sendMessage"] and "api" in CALLS["sendMessage"][-1]["text"] and "✓ " in CALLS["sendMessage"][-1]["text"] and "✗ " in CALLS["sendMessage"][-1]["text"] and all(icon_in(l) for l in CALLS["sendMessage"][-1]["text"].splitlines()) and all(len(l) <= 22 for l in CALLS["sendMessage"][-1]["text"].splitlines()), r.stdout + r.stderr + str(CALLS["sendMessage"][-1:]))
CALLS.pop("setMyCommands", None)
r = bot("install")
T.check("W7 install: poll cron + digest cron at 08:00, setMyCommands called", r.returncode == 0 and "bot ensure" in cron.read_text() and "0 8 * * *" in cron.read_text() and "bot digest" in cron.read_text() and CALLS.get("setMyCommands"), r.stdout + r.stderr + cron.read_text() + str(CALLS.get("setMyCommands")))
bot("uninstall")   # ferma il daemon che install ha avviato
TMUX.__exit__(None, None, None)
T.rm(tmp)
T.finish()

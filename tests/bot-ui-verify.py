#!/usr/bin/env python3
"""Verifica cm-bot-ui.py: le funzioni PURE del bot a misura di smartwatch (mandato dell'utente 11/09 16:36-16:40).

U1  normalizzazione della dettatura: minuscole, via punteggiatura, numeri in parole → cifre, si/sì → sì,
    prima parola utile («due si» → 2, «tre» → 3, «Si.» → sì, «Sessioni!» → sessioni)
U2  comandi a parola nuda e alias: sessioni/s, master/m, lancia X/l X, quota/q, aiuto/?, elenco/0, segui/f,
    riprendi/t, full; le forme /comando restano; un numero → ("number", n); sì → ("yes",)
U3  elenco: una riga per sessione «N <stato> <nome corto>» col numero ESPLICITO (l'utente 18:15: niente da
    contare) + due parole della domanda sulle ❓, età compatta sulle ✓ ferme da > 1 giorno; ordine ❓ ▶ ✓ ✗
    poi alfabetico, nomi senza prefisso account troncati a 14, OGNI riga ≤ 22 caratteri, ≤ 8 righe (oltre: «+N»)
U4  scheda: ≤ 8 righe, ≤ 22 caratteri, nome + account, stato, esito in ≤ 2 righe, prossimo, domanda con
    opzioni numerate una per riga
U5  tastiere (nomi del 12/09/2026: imperativo = azione, sostantivo = destinazione): elenco = UN bottone per
    sessione, uno per riga, etichetta «<stato> <nome corto>» ≤ 22, callback n:i, poi Recap/Quota/Avvia master;
    scheda = un bottone per opzione («1 A»), Avvisami/Basta avvisi, Continua solo su ✓/✗, Annulla modifiche solo
    con un checkpoint, Sessioni; messaggio vivo = Ferma/Terminale/Sessioni; «◀ nome» + Sessioni sotto il resto;
    esito = Leggi tutto (se tagliato)/Apri nome/Basta avvisi; digest = Apri per ogni ❓ (max 4)
U7  messaggio vivo: eventi del transcript (user/text/tool), riga del tool, righe vive, riga «Watch:»
U8  lettura vocale (Handwave, via master 12/09): la riga «Esito:» è UNA frase ≤ 120 caratteri, senza elenchi né
    percorsi; l'avviso di domanda ha al massimo 3 bottoni-opzione (oltre: i primi due + Apri); la scheda apre con
    l'icona di stato («▶ 🔴 master»)
U6  età compatta: 45m, 3h, 2g
"""
import importlib.util
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

spec = importlib.util.spec_from_file_location("cm_bot_ui", T.SCRIPTS / "cm-bot-ui.py")
ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)

# U1
T.check("U1 «due si» → tokens [2, sì]", ui.normalize("Due si") == ["2", "sì"], str(ui.normalize("Due si")))
T.check("U1 «tre» → 3; «Si.» → sì; «Sessioni!» → sessioni", ui.normalize("tre") == ["3"] and ui.normalize("Si.") == ["sì"] and ui.normalize("Sessioni!") == ["sessioni"], str([ui.normalize(x) for x in ("tre", "Si.", "Sessioni!")]))
T.check("U1 accents and mixed: «Lancia Shop-acme, grazie» → [lancia, api, grazie]; «dieci» → 10", ui.normalize("Lancia Shop-acme, grazie") == ["lancia", "shop-acme", "grazie"] and ui.normalize("dieci") == ["10"], str(ui.normalize("Lancia Shop-acme, grazie")))
# U2
P = ui.parse_command
T.check("U2 bare words: sessioni/s → sessions", P("sessioni") == ("sessions", "") and P("s") == ("sessions", "") and P("/sessions") == ("sessions", ""), str((P("sessioni"), P("s"))))
T.check("U2 /sessions full and «sessioni full» → (sessions, full)", P("/sessions full") == ("sessions", "full") and P("sessioni full") == ("sessions", "full"), str(P("sessioni full")))
T.check("U2 lancia shop-acme / l shop-acme / /launch shop-acme → (launch, api)", P("lancia shop-acme") == ("launch", "shop-acme") and P("l shop-acme") == ("launch", "shop-acme") and P("/launch shop-acme") == ("launch", "shop-acme"), str(P("lancia shop-acme")))
T.check("U2 master/m, quota/q, aiuto/?, elenco/0, segui/f, riprendi/t, /start", P("master") == ("master", "") and P("m") == ("master", "") and P("r") == ("recap", "") and P("recap") == ("recap", "") and P("q") == ("quota", "") and P("aiuto") == ("help", "") and P("?") == ("help", "") and P("elenco") == ("list", "") and P("0") == ("list", "") and P("f") == ("follow", "") and P("segui") == ("follow", "") and P("t") == ("resume", "") and P("riprendi") == ("resume", "") and P("/start") == ("start", ""), str([P(x) for x in ("m", "q", "?", "0", "f", "t")]))
T.check("U2 «due si» → (number, 2); «3» → (number, 3); «si» → (yes, ''); «boh» → (text, boh)", P("due si") == ("number", 2) and P("3") == ("number", 3) and P("si") == ("yes", "") and P("boh") == ("text", "boh"), str((P("due si"), P("si"), P("boh"))))
T.check("U2 /master@bot → master", P("/master@cm_bot") == ("master", ""), str(P("/master@cm_bot")))
# U3
now = time.time()
rows = [
    {"tmux": "pix-acme-warehouse-it", "name": "acme-warehouse-it", "status": "idle", "account": "professionale", "waiting": False, "last_ts": now - 2 * 86400 - 100},
    {"tmux": "api", "name": "api", "status": "waiting", "account": "personale", "waiting": True, "question": "Vuoi A oppure B per il deploy?"},
    {"tmux": "master", "name": "master", "status": "busy", "account": "personale", "waiting": False},
    {"tmux": "chrome-bridge", "name": "chrome-bridge", "status": "idle", "account": "personale", "waiting": False, "last_ts": now - 3600},
    {"tmux": "pix-api-gateway-it", "name": "api-gateway-it", "status": "busy", "account": "professionale", "waiting": False},
    {"tmux": "vecchia", "name": "vecchia", "status": "dead", "account": "personale", "waiting": False},
]
ordered = ui.order_rows(rows)
T.check("U3 order ❓ ▶ ✓ ✗ then alphabetical", [r["tmux"] for r in ordered] == ["api", "pix-api-gateway-it", "master", "pix-acme-warehouse-it", "chrome-bridge", "vecchia"], str([r["tmux"] for r in ordered]))
IC = {"api": "🟢", "master": "🔴", "pix-api-gateway-it": "🟥", "chrome-bridge": "🟡", "pix-acme-warehouse-it": "🟧", "vecchia": "🟣"}
lines = ui.list_lines(ordered, prefixes=["pix-"], now=now, icons=IC)
T.check("U3 every line ≤ 22 chars, one per session", len(lines) == 6 and all(len(l) <= 22 for l in lines), str([(len(l), l) for l in lines]))
T.check("U3 numbered lines: ❓ carries two words of the question; the age only when there is room (icon + 14-char name fill the line)", lines[0].startswith("1 ❓ 🟢 api") and "Vuoi A" in lines[0] and lines[3] == "4 ✓ 🟧 acme-warehouse", str(lines))
T.check("U3 ▶ and fresh ✓ lines are bare; prefix stripped; name cut at 14", lines[1] == "2 ▶ 🟥 api-gateway-it" and lines[2] == "3 ▶ 🔴 master" and lines[4] == "5 ✓ 🟡 chrome-bridge" and lines[5] == "6 ✗ 🟣 vecchia", str(lines))
many = [{"tmux": f"s{i:02d}", "name": f"s{i:02d}", "status": "idle", "account": "personale", "waiting": False} for i in range(11)]
lines = ui.list_lines(ui.order_rows(many), prefixes=[], now=now)
T.check("U3 more than 8: 7 lines + «+4»; lines still numbered", len(lines) == 8 and lines[-1].startswith("+4") and lines[6].startswith("7 "), str(lines))
labels = ui.list_labels(ui.order_rows(rows), prefixes=["pix-"], icons=IC)
T.check("U3 button labels «<stato> <icona> <nome corto>», one per session, ≤ 22", labels[:2] == ["❓ 🟢 api", "▶ 🟥 api-gateway-it"] and len(labels) == 6 and all(len(l) <= 22 for l in labels), str(labels))
# U4 (l'utente 18:24): struttura fissa della scheda
now = time.time()
card = ui.card_lines({"tmux": "pix-api", "name": "api", "status": "waiting", "account": "professionale", "waiting": True, "last_ts": now - 720},
                     esito="Esito: deploy **pronto** sul `server` di prova, manca la [conferma](https://x) del cliente per pubblicare",
                     next_step="Attendere la conferma del cliente", question="Vuoi A oppure B?", options=["A", "B"], prefixes=["pix-"], now=now, icon="🟩")
T.check("U4 card, full width (12/09 11:14): whole lines, no «…», first line «❓ 🟩 api · aspetta te», the esito whole, «→ prossimo» whole, the question", card[0] == "❓ 🟩 api · aspetta te" and card[1].startswith("deploy pronto sul server di prova, manca la conferma del cliente") and not any("…" in l for l in card) and "prof" not in " ".join(card), str(card))
T.check("U4 recap «→ prossimo», then the question; two options → no option lines (buttons suffice)", any(l.startswith("→ Attendere") for l in card) and card[-1] == "Vuoi A oppure B?" and not any(l.startswith("1 A") for l in card), str(card))
card2 = ui.card_lines({"tmux": "master", "name": "master", "status": "busy", "account": "personale", "waiting": False, "last_ts": now - 200}, esito="qualcosa", next_step="", prefixes=[], now=now)
T.check("U4 busy > 2 min: «▶ master · lavora 3m» and NO esito line (the duration is in the head); no recap → «→ nessun recap»", card2[0] == "▶ master · lavora 3m" and card2[1] == "→ nessun recap" and len(card2) == 2, str(card2))
card3 = ui.card_lines({"tmux": "vecchia", "name": "vecchia", "status": "dead", "account": "personale", "waiting": False, "visto_ts": now - 3600}, esito="", next_step="", prefixes=[], now=now)
T.check("U4 dead: «✗ vecchia · sparita 1h»", card3[0] == "✗ vecchia · sparita 1h", str(card3))
long_esito = "Esito: " + "deploy pronto sul server di prova, cache svuotata, manca la conferma del cliente per pubblicare in produzione; " * 2
card4 = ui.card_lines({"tmux": "pix-ledger", "name": "ledger", "status": "idle", "account": "professionale", "waiting": False, "last_ts": now - 60},
                      esito=long_esito, next_step="Attendere la conferma del cliente, poi pubblicare e avvisare il team di supporto", prefixes=["pix-"], now=now, max_lines=20)
T.check("U4c a long esito and a long next step stay WHOLE on one line each, no «…» (full width, 12/09)", len(card4) == 3 and card4[0] == "✓ ledger · ferma 1m" and len(card4[1]) > 100 and card4[2].startswith("→ ") and not any("…" in l for l in card4), str(card4))
T.check("U4 strip_markdown", ui.strip_markdown("**a** `b` [c](http://x) _d_ ## e") == "a b c d e", ui.strip_markdown("**a** `b` [c](http://x) _d_ ## e"))
T.check("U4 esito_of: the «Esito:» line wins, else the last text line", ui.esito_of("bla\nEsito: fatto tutto\nresto", "resto") == "fatto tutto" and ui.esito_of("prima riga\nultima riga", "ultima riga") == "ultima riga", str(ui.esito_of("bla\nEsito: fatto tutto\nresto", "resto")))
# U5 (l'utente 21:00): tasti CONTESTUALI, mai piu' di quattro in fondo, mai un tasto che ripete quello che hai davanti
kb = ui.keyboard_list(ui.list_labels(ui.order_rows(many), prefixes=[]), master_alive=False)
rows_ = kb["inline_keyboard"]
T.check("U5 list keyboard: one name button per row (11), then Recap / Quota / Avvia master (master not alive), all alone", len(rows_) == 14 and rows_[0] == [{"text": "✓ s00", "callback_data": "n:1"}] and [r[0]["text"] for r in rows_[-3:]] == ["Recap", "Quota", "Avvia master"] and all(len(r) == 1 for r in rows_), str(rows_[-4:]))
kb2 = ui.keyboard_list(["▶ 🔴 master"], master_alive=True)
T.check("U5 list keyboard with the master alive: no «Avvia master»", [r[0]["text"] for r in kb2["inline_keyboard"]] == ["▶ 🔴 master", "Recap", "Quota"], str(kb2))
kbc = ui.keyboard_card(["A", "B"], following=False)
texts = [[b["text"] for b in row] for row in kbc["inline_keyboard"]]
T.check("U5 card keyboard (idle, no checkpoint): options one per row, then Avvisami, Continua, Sessioni — no Quota/Master", texts == [["1 A"], ["2 B"], ["Avvisami"], ["Continua"], ["Sessioni"]], str(texts))
kbc2 = ui.keyboard_card([], following=True, state="busy", has_checkpoint=True)
texts2 = [[b["text"] for b in row] for row in kbc2["inline_keyboard"]]
T.check("U5 following + busy + checkpoint → Basta avvisi (callback follow), no Continua, Annulla modifiche (rollback), Sessioni", texts2 == [["Basta avvisi"], ["Annulla modifiche"], ["Sessioni"]] and kbc2["inline_keyboard"][0][0]["callback_data"] == "follow" and kbc2["inline_keyboard"][1][0]["callback_data"] == "rollback", str(kbc2))
T.check("U5 Continua also on a ✗ session, never on ❓", [r[0]["text"] for r in ui.keyboard_card([], state="dead")["inline_keyboard"]] == ["Avvisami", "Continua", "Sessioni"] and "Continua" not in [r[0]["text"] for r in ui.keyboard_card(["A"], state="waiting")["inline_keyboard"]], "")
kba = ui.keyboard_live("pix-api", "🟦 api")
T.check("U5 live message: Ferma (stop:) / Terminale (screen:) / Sessioni; keyboard_after_send is its alias", [[b["text"] for b in r] for r in kba["inline_keyboard"]] == [["Ferma"], ["Terminale"], ["Sessioni"]] and kba["inline_keyboard"][0][0]["callback_data"] == "stop:pix-api" and kba["inline_keyboard"][1][0]["callback_data"] == "screen:pix-api" and ui.keyboard_after_send("pix-api", "🟦 api") == kba, str(kba))
T.check("U5 back keyboard: «◀ 🟦 api» (card:) + Sessioni; without a name only Sessioni; keyboard_fixed is its alias", [[b["text"] for b in r] for r in ui.keyboard_back("pix-api", "🟦 api")["inline_keyboard"]] == [["◀ 🟦 api"], ["Sessioni"]] and ui.keyboard_back("pix-api", "🟦 api")["inline_keyboard"][0][0]["callback_data"] == "card:pix-api" and [[b["callback_data"] for b in r] for r in ui.keyboard_back()["inline_keyboard"]] == [["list"]] and ui.keyboard_fixed() == ui.keyboard_back(), str(ui.keyboard_back("pix-api", "🟦 api")))
T.check("U5 retry keyboard: Invia di nuovo (retry:) / Sessioni", [[b["text"] for b in r] for r in ui.keyboard_retry("pix-api")["inline_keyboard"]] == [["Invia di nuovo"], ["Sessioni"]] and ui.keyboard_retry("pix-api")["inline_keyboard"][0][0]["callback_data"] == "retry:pix-api", "")
T.check("U5 start keyboard: Avvia master (if missing) + Sessioni", [[b["text"] for b in r] for r in ui.keyboard_start(False)["inline_keyboard"]] == [["Avvia master"], ["Sessioni"]] and [[b["text"] for b in r] for r in ui.keyboard_start(True)["inline_keyboard"]] == [["Sessioni"]], "")
T.check("U5 digest keyboard: «Apri <label>» per ❓ (max 4) then Sessioni", [[b["text"] for b in r] for r in ui.keyboard_digest(["a", "b", "c", "d", "e"], lambda n: "🟦 " + n)["inline_keyboard"]] == [["Apri 🟦 a"], ["Apri 🟦 b"], ["Apri 🟦 c"], ["Apri 🟦 d"], ["Sessioni"]], "")
T.check("U8 voice_line: first sentence only, ≤ 120, bullets and paths stripped", ui.voice_line("Fatto tutto. Poi altro ancora.") == "Fatto tutto." and ui.voice_line("- fatto in /home/x/y.py e /tmp/z") == "fatto in y.py e z" and len(ui.voice_line("parola " * 40)) <= 120 and ui.voice_line("parola " * 40).endswith("…") and ui.voice_line("Senza punto finale") == "Senza punto finale" and ui.voice_line("Ok! Poi") == "Ok!", str([ui.voice_line("Fatto tutto. Poi altro ancora."), ui.voice_line("- fatto in /home/x/y.py e /tmp/z")]))
kbq = ui.keyboard_notice("pix-api", ["a"], "🟦 api", full_question=True)
T.check("U8 notice keyboard with a cut question: «Domanda intera» (q:name) before Apri; card keyboard likewise (q:)", [[b["text"] for b in r] for r in kbq["inline_keyboard"]] == [["1 a"], ["Domanda intera"], ["Apri 🟦 api"]] and kbq["inline_keyboard"][1][0]["callback_data"] == "q:pix-api" and [r[0]["text"] for r in ui.keyboard_card(["a"], state="waiting", full_question=True)["inline_keyboard"]] == ["1 a", "Domanda intera", "Avvisami", "Sessioni"], str(kbq))
T.check("U10 link_button: app → «Apri sessione» https; browser → «Apri in Chrome» intent://…;end; no link → None; intent_url shape", ui.link_button("https://claude.ai/code/s1", "app") == [{"text": "Apri sessione", "url": "https://claude.ai/code/s1"}] and ui.link_button("https://claude.ai/code/s1", "browser") == [{"text": "Apri in Chrome", "url": "intent://claude.ai/code/s1#Intent;scheme=https;package=com.android.chrome;end"}] and ui.link_button("", "app") is None, str(ui.link_button("https://claude.ai/code/s1", "browser")))
T.check("U10 card and notice keyboards carry the link row (card: before Sessioni, not on ✗; notice: after Apri)", [r[0]["text"] for r in ui.keyboard_card([], link="https://x/y")["inline_keyboard"]] == ["Avvisami", "Continua", "Apri sessione", "Sessioni"] and "Apri sessione" not in [r[0]["text"] for r in ui.keyboard_card([], state="dead", link="https://x/y")["inline_keyboard"]] and [r[0]["text"] for r in ui.keyboard_notice("s", ["a"], "s", link="https://x/y", link_mode="browser")["inline_keyboard"]] == ["1 a", "Apri s", "Apri in Chrome"], "")
kbn4 = ui.keyboard_notice("pix-api", ["a", "b", "c", "d"], "🟦 api")
T.check("U8 notice keyboard: 4 options → first two + Apri (3 buttons); 3 options → all three + Apri", [[b["text"] for b in r] for r in kbn4["inline_keyboard"]] == [["1 a"], ["2 b"], ["Apri 🟦 api"]] and len(ui.keyboard_notice("pix-api", ["a", "b", "c"])["inline_keyboard"]) == 4, str(kbn4))
cl = ui.card_lines({"tmux": "master", "name": "master", "status": "busy", "account": "personale", "waiting": False, "last_ts": now - 200}, esito="Esito: x", now=now, icon="🔴")
T.check("U8 card opens with the state icon and the state on the same line: «▶ 🔴 master · lavora 3m»", cl[0] == "▶ 🔴 master · lavora 3m", str(cl))
kbn = ui.keyboard_notice("pix-api", ["yes", "no"], "🟦 api")
T.check("U5 question notice: the options then «Apri 🟦 api»", [[b["text"] for b in r] for r in kbn["inline_keyboard"]] == [["1 yes"], ["2 no"], ["Apri 🟦 api"]] and kbn["inline_keyboard"][0][0]["callback_data"] == "ans:pix-api:1" and kbn["inline_keyboard"][2][0]["callback_data"] == "card:pix-api", str(kbn))
kbo = ui.keyboard_outcome("pix-api", "🟦 api")
T.check("U5 outcome: Apri 🟦 api / Basta avvisi; cut → Leggi tutto (full:) first", [[b["text"] for b in r] for r in kbo["inline_keyboard"]] == [["Apri 🟦 api"], ["Basta avvisi"]] and [[b["callback_data"] for b in r] for r in kbo["inline_keyboard"]] == [["card:pix-api"], ["unfollow:pix-api"]] and [[b["text"] for b in r] for r in ui.keyboard_outcome("pix-api", "🟦 api", cut=True)["inline_keyboard"]] == [["Leggi tutto"], ["Apri 🟦 api"], ["Basta avvisi"]] and ui.keyboard_outcome("pix-api", cut=True)["inline_keyboard"][0][0]["callback_data"] == "full:pix-api", str(kbo))
T.check("U2 new dictable words: avvisami → follow, continua → resume, ferma/stop → stop, terminale → screen, riprova → retry; old aliases kept", P("avvisami") == ("follow", "") and P("continua") == ("resume", "") and P("ferma") == ("stop", "") and P("stop") == ("stop", "") and P("terminale") == ("screen", "") and P("riprova") == ("retry", "") and P("segui") == ("follow", "") and P("riprendi") == ("resume", "") and P("schermo") == ("screen", "") and P("annulla") == ("rollback", ""), str([P(x) for x in ("avvisami", "continua", "ferma", "terminale")]))
T.check("U9 question_gist: whole when it fits; else the last interrogative sentence that fits; else empty", ui.question_gist("Vuoi A o B?") == "Vuoi A o B?" and ui.question_gist("Il PC non accetta connessioni in entrata, quindi serve un altro canale. Da dove passano stato e comandi?") == "Da dove passano stato e comandi?" and ui.question_gist("x" * 100) == "", str(ui.question_gist("Il PC non accetta connessioni in entrata, quindi serve un altro canale. Da dove passano stato e comandi?")))
cq = ui.card_lines({"tmux": "api", "name": "api", "status": "waiting", "account": "personale", "waiting": True}, question="Il PC non accetta connessioni in entrata, quindi serve un altro canale. Da dove passano stato e comandi fra PC e orologio?", options=["A", "B"], now=now, icon="🟩")
T.check("U9 card: the question's gist wrapped whole over ≤ 3 lines; two options → no option lines (the buttons suffice); five → listed", "Da dove passano stato e comandi fra PC e orologio?" in " ".join(cq) and not any("…" in l for l in cq) and not any(l.startswith("1 A") for l in cq) and ui.card_lines({"tmux": "api", "name": "api", "status": "waiting", "account": "personale", "waiting": True}, question="Quale?", options=list("ABCDE"), now=now, icon="🟩")[-5:] == ["1 A", "2 B", "3 C", "4 D", "5 E"], str(cq))
# U7 messaggio vivo
import json as _json, tempfile as _tf, os as _os
tp = _os.path.join(_tf.mkdtemp(prefix="cm-ui-"), "t.jsonl")
with open(tp, "w") as f:
    f.write(_json.dumps({"type": "user", "message": {"role": "user", "content": "Dall'utente via Telegram (watch). fai X"}}) + "\n")
    f.write("riga rotta\n")
    f.write(_json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Cerco il **codice**…"}, {"type": "tool_use", "name": "Bash", "input": {"command": "git log --since yesterday --oneline", "description": "x"}}]}}) + "\n")
    f.write(_json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}}) + "\n")
ev, off = ui.transcript_events(tp, 0)
T.check("U7 transcript events: user text, assistant text, tool_use (name + input); tool_result and broken lines ignored; offset = file size", [e[:2] for e in ev] == [("user", "Dall'utente via Telegram (watch). fai X"), ("text", "Cerco il **codice**…"), ("tool", "Bash")] and ev[2][2]["command"].startswith("git log") and off == _os.path.getsize(tp), str(ev) + str(off))
ev2, off2 = ui.transcript_events(tp, off)
T.check("U7 from the offset: nothing new; missing file: ([], offset)", ev2 == [] and off2 == off and ui.transcript_events(tp + ".nope", 5) == ([], 5), str(ev2))
T.check("U10 tool_note: Bash description trimmed to one line and 120 chars; nothing without it", ui.tool_note({"command": "x", "description": "  Run  the suite\n"}) == "Run the suite" and ui.tool_note({"command": "x"}) == "" and ui.tool_note(None) == "" and len(ui.tool_note({"description": "y" * 300})) == 120, "")
T.check("U7 tool_line: «Bash git log --since yesterday --oneline» whole (no «…»), file_path for Read; plain when no known key; input capped at 120 without a cut mark", ui.tool_line("Bash", {"command": "git log --since yesterday --oneline"}) == "Bash git log --since yesterday --oneline" and ui.tool_line("Read", {"file_path": "/a/b.py"}) == "Read /a/b.py" and ui.tool_line("Agent", {"x": 1}) == "Agent" and len(ui.tool_line("Bash", {"command": "x" * 200})) == 125 and "…" not in ui.tool_line("Bash", {"command": "x" * 200}), str(ui.tool_line("Bash", {"command": "git log --since yesterday --oneline"})))
ll = ui.live_lines("🔴 master", 30, "Bash git status", "Cerco le voci di ieri nel recap, poi riassumo tutto in due righe")
T.check("U7 live lines, full width: «▶ nome · al lavoro · Bash git status» in ONE line, then the text whole", ll == ["▶ 🔴 master · al lavoro · Bash git status", "Cerco le voci di ieri nel recap, poi riassumo tutto in due righe"], str(ll))
T.check("U7 live lines after a minute: «▶ nome · 1m»; nothing else without tool/text; line/join helpers", ui.live_lines("🔴 master", 75) == ["▶ 🔴 master · 1m"] and ui.line("  a   b\nc ") == "a b c" and ui.join("x", "", " y ") == "x · y", str(ui.live_lines("🔴 master", 75)))
T.check("U7 watch_line: the last «Watch:» line, markdown stripped, case-insensitive; empty when missing", ui.watch_line("Esito: x\n**Watch:** tutto _verde_, 3 file\n") == "tutto verde, 3 file" and ui.watch_line("bla\nwatch : a\nWATCH: b") == "b" and ui.watch_line("niente") == "", str(ui.watch_line("Esito: x\n**Watch:** tutto _verde_, 3 file\n")))

# U6
T.check("U3b list header ≤ 22: «6 sessioni · 1❓ · 1✗»; no ✗ → no ✗ part", ui.list_header(ordered) == "6 sessioni · 1❓ · 1✗" and ui.list_header(ordered[:5]) == "5 sessioni · 1❓" and ui.list_header(ordered[1:5]) == "4 sessioni" and len(ui.list_header(ordered)) <= 22, str(ui.list_header(ordered)))
names_ = [r["tmux"] for r in ordered]
T.check("U3b match_session: exact short name wins; unique prefix without pix-; ambiguous → candidates; none → []", ui.match_session("api", names_, ["pix-"]) == ["api"] and ui.match_session("api-g", names_, ["pix-"]) == ["pix-api-gateway-it"] and ui.match_session("acme-w", names_, ["pix-"]) == ["pix-acme-warehouse-it"] and set(ui.match_session("a", names_, ["pix-"])) == {"api", "pix-api-gateway-it", "pix-acme-warehouse-it"} and ui.match_session("zzz", names_, ["pix-"]) == [], str(ui.match_session("a", names_, ["pix-"])))
T.check("U6 compact age", ui.age_compact(45 * 60) == "45m" and ui.age_compact(3 * 3600 + 100) == "3h" and ui.age_compact(2 * 86400 + 5) == "2g", str([ui.age_compact(x) for x in (2700, 10900, 172805)]))
T.finish()

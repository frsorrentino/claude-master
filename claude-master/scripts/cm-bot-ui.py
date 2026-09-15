#!/usr/bin/env python3
"""Le funzioni PURE del bot a misura di smartwatch (Wear OS, mandato dell'utente dell'11/09/2026 16:36–16:40).

Misure prese dal polso: ~22 caratteri utili per riga, 8-10 righe visibili, il client rende le tastiere
inline e il tocco arriva (callback_query); la dettatura scrive minuscole senza accenti e concatena
(«due si»). Dal 12/09 (screenshot dell'utente): la bolla di Telegram è larga quanto la riga più lunga, quindi nel CORPO
niente a capo né tagli a 22 (line/join: righe intere, le corte unite con « · », la prima la più lunga); i 22
caratteri restano la misura delle ETICHETTE dei bottoni (fit). Comandi a parola nuda dettabile oltre a /comando;
numeri sempre accettati come alternativa ai bottoni; una riga contestuale di bottoni (Sessioni, «◀ nome»,
Avvisami, Continua, Ferma, Terminale…), così dal polso non si scrive mai.

Niente I/O qui: cm-bot.py chiama, questo modulo rende e interpreta. Testato in tests/bot-ui-verify.py.
"""
import json
import re
import unicodedata

WIDTH = 22
MAX_LINES = 8
NAME_MAX = 14

NUMERI = {"zero": "0", "uno": "1", "una": "1", "due": "2", "tre": "3", "quattro": "4", "cinque": "5", "sei": "6",
          "sette": "7", "otto": "8", "nove": "9", "dieci": "10"}
# parola nuda (o alias) → comando; le forme /comando passano dallo stesso dizionario senza la barra
PAROLE = {"sessions": "sessions", "sessioni": "sessions", "s": "sessions",
          "master": "master", "m": "master",
          "launch": "launch", "lancia": "launch", "l": "launch",
          "quota": "quota", "q": "quota",
          "help": "help", "aiuto": "help", "?": "help",
          "list": "list", "elenco": "list", "0": "list",
          "follow": "follow", "segui": "follow", "f": "follow",
          "resume": "resume", "riprendi": "resume", "t": "resume", "reopen": "reopen", "riapri": "reopen",
          "recap": "recap", "diary": "recap", "diario": "recap", "r": "recap",
          "start": "start", "screen": "screen", "schermo": "screen", "terminale": "screen", "v": "screen",
          "rollback": "rollback", "annulla": "rollback",
          # i nomi nuovi dei tasti (12/09/2026: imperativo = azione, sostantivo = destinazione); i vecchi restano alias
          "avvisami": "follow", "continua": "resume", "ferma": "stop", "stop": "stop", "riprova": "retry"}
STATI = {"waiting": "❓", "busy": "▶", "idle": "✓", "dead": "✗"}
ORDINE = {"waiting": 0, "busy": 1, "idle": 2, "dead": 3}


# ------------------------------------------------------------------ dettatura
def _senza_accenti(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(text):
    """«Due si» → ["2", "sì"]: minuscole, punteggiatura via, numeri in parole → cifre, si → sì.
    Il «?» da solo resta (è l'alias di aiuto)."""
    t = (text or "").strip().lower()
    if t == "?":
        return ["?"]
    t = re.sub(r"[^\w\s?/@-]", " ", t)
    out = []
    for w in t.split():
        base = _senza_accenti(w)
        if base == "si":
            out.append("sì")
        elif base in NUMERI:
            out.append(NUMERI[base])
        else:
            out.append(w)
    return out


def parse_command(text):
    """(comando, resto). «lancia shop-acme» → ("launch", "shop-acme"); «/sessions full» → ("sessions", "full");
    «due si» → ("number", 2); «si» → ("yes", ""); altro → ("text", prima parola)."""
    toks = normalize(text)
    if not toks:
        return ("text", "")
    head = toks[0]
    if head.startswith("/"):
        head = head[1:].split("@", 1)[0]
    if head in PAROLE:
        return (PAROLE[head], " ".join(toks[1:]))
    if head.isdigit():
        return ("number", int(head))
    if head == "sì":
        return ("yes", "")
    return ("text", head)


# ------------------------------------------------------------------ resa
def age_compact(seconds):
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}g"


def short_name(name, prefixes=(), n=NAME_MAX):
    for p in sorted((p for p in prefixes if p), key=len, reverse=True):
        if name.startswith(p):
            name = name[len(p):]
            break
    return name[:n]


def state_of(row):
    if row.get("status") == "dead":
        return "dead"
    if row.get("waiting") or row.get("status") == "waiting":
        return "waiting"
    if row.get("status") == "busy":
        return "busy"
    return "idle"


def order_rows(rows):
    return sorted(rows, key=lambda r: (ORDINE[state_of(r)], (r.get("name") or r.get("tmux") or "").lower()))


def fit(text, width=WIDTH):
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def line(text):
    """Una riga logica = una riga fisica (l'utente 12/09 11:14, screenshot di telefono e watch): spazi normalizzati,
    NESSUN taglio ne' a capo interno — la bolla di Telegram si allarga quanto la riga piu' lunga, e a 22
    caratteri restava a meta' schermo col testo mozzato. Solo le etichette dei bottoni passano da fit()."""
    return " ".join(str(text or "").split())


def join(*parts):
    """Righe corte adiacenti dello stesso tipo unite con « · », cosi' la prima riga e' la piu' lunga possibile."""
    return " · ".join(line(p) for p in parts if line(p))


def wrap(text, width=WIDTH, max_lines=2):
    """Spezza sulle parole; oltre max_lines tronca con «…»."""
    words = str(text or "").split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w[:width]
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w[:width]
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if len(words) and " ".join(lines).split() != words:
        lines[-1] = fit(lines[-1] + "…", width) if not lines[-1].endswith("…") else lines[-1]
    return lines


def _icon(r, icons):
    """L'icona della sessione dal registro colori (l'utente 18:27): tondo = personale, quadrato =
    professionale, lo stesso della scheda del Terminale. Porta account e identita': niente «pers/prof»."""
    ic = (icons or {}).get(r.get("tmux") or "") or (icons or {}).get(r.get("name") or "") or ""
    return f"{ic} " if ic else ""


def list_labels(rows, prefixes=(), width=WIDTH, icons=None):
    """L'etichetta del bottone di ogni sessione: «<stato> <icona> <nome corto>» (l'utente 18:15: si tocca il
    nome, niente da contare)."""
    return [fit(f"{STATI[state_of(r)]} {_icon(r, icons)}{short_name(r.get('name') or r.get('tmux') or '?', prefixes)}", width) for r in rows]


def list_lines(rows, prefixes=(), now=None, width=WIDTH, max_lines=MAX_LINES, icons=None):
    """Una riga per sessione, col numero ESPLICITO davanti: «N <stato> <nome corto>» + due parole della
    domanda sulle ❓, l'età compatta sulle ✓ ferme da più di un giorno (chi detta dice «tre» e sa a cosa
    corrisponde). Ogni riga ≤ width; oltre max_lines: max_lines-1 righe + «+N»."""
    out = []
    for i, r in enumerate(rows, 1):
        st = state_of(r)
        base = f"{i} {STATI[st]} {_icon(r, icons)}{short_name(r.get('name') or r.get('tmux') or '?', prefixes)}"
        extra = ""
        if st == "waiting" and r.get("question"):
            extra = " ".join(str(r["question"]).split()[:2])
        elif st == "idle" and now and r.get("last_ts") and now - float(r["last_ts"]) > 86400:
            extra = age_compact(now - float(r["last_ts"]))
        line = base
        if extra:
            room = width - len(base) - 1
            if room >= 2:
                line = base + " " + (extra if len(extra) <= room else extra[:room - 1] + "…")
        out.append(line[:width])
    if len(out) > max_lines:
        rest = len(out) - (max_lines - 1)
        out = out[:max_lines - 1] + [f"+{rest}"]
    return out


def strip_markdown(text):
    """Via backtick, asterischi, underscore, titoli, elenchi; i link restano col solo nome."""
    t = str(text or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`+", "", t)
    t = re.sub(r"\*+", "", t)
    t = re.sub(r"(?<!\w)_+|_+(?!\w)", "", t)
    t = re.sub(r"^\s*(#+|[-*>]+)\s*", "", t, flags=re.M)
    t = re.sub(r"\s+#+\s+", " ", t)
    return " ".join(t.split())


def esito_of(last, tail=""):
    """L'ultimo esito di una sessione (regola 9): la riga «Esito:» se c'è (nel messaggio o nella coda
    salvata dall'hook), altrimenti l'ultima riga di testo; senza markdown."""
    for src in (tail, last):
        for l in reversed(str(src or "").splitlines()):
            m = re.match(r"^\s*\W*\s*esito\s*:\s*(.+)$", l, flags=re.I)
            if m:
                return strip_markdown(m.group(1))
    lines = [l for l in str(tail or last or "").splitlines() if l.strip() and not l.strip().startswith("```")]
    return strip_markdown(lines[-1]) if lines else ""


def cut44(text, n=44):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def card_lines(row, esito="", next_step="", question="", options=(), prefixes=(), now=None, width=WIDTH, max_lines=MAX_LINES, icon=""):
    """La scheda (l'utente 18:24; larghezza piena 12/09 11:14): righe INTERE, mai spezzate.
    1 «<stato> <icona> nome · stato da quanto» · 2 l'ultimo esito (taciuto se lavora da > 2 min: la durata e'
    gia' in testa) · 3 «→ prossimo» del recap (o «→ nessun recap») · poi la domanda (il suo succo) e, se sono piu'
    di tre, le opzioni una per riga (fino a tre bastano i bottoni)."""
    now = now or 0
    st = state_of(row)
    nome = short_name(row.get("name") or row.get("tmux") or "?", prefixes)
    esito = esito_of(esito) if esito else ""
    since = None
    if st in ("busy", "idle") and row.get("last_ts") and now:
        since = now - float(row["last_ts"])
    elif st == "dead" and row.get("visto_ts") and now:
        since = now - float(row["visto_ts"])
    eta = f" {age_compact(since)}" if since is not None else ""
    stato = {"waiting": "aspetta te", "busy": f"lavora{eta}", "idle": f"ferma{eta}", "dead": f"sparita{eta}"}[st]
    lines = [join(f"{STATI[st]} {icon} {nome}".replace("  ", " ").strip(), stato)]
    if esito and not (st == "busy" and since is not None and since >= 120):
        lines.append(line(strip_markdown(esito)))
    lines.append(line(f"→ {strip_markdown(next_step)}") if next_step else "→ nessun recap")
    if question:
        lines.append(line(question_gist(question) or question))
        if len(options) > 3:
            lines += [line(f"{i + 1} {o}") for i, o in enumerate(options)]
    return lines[:max_lines]


def list_header(rows):
    """La riga di testa dell'elenco (l'utente 18:28: le righe di testo erano ridondanti coi bottoni):
    «N sessioni · k aspetta te · j sparite»."""
    n = len(rows)
    k = sum(1 for r in rows if state_of(r) == "waiting")
    j = sum(1 for r in rows if state_of(r) == "dead")
    # «aspetta te»/«sparite» per esteso non ci stanno in 22 caratteri: le icone dicono lo stesso
    parts = [f"{n} sessioni"] + ([f"{k}❓"] if k else []) + ([f"{j}✗"] if j else [])
    return fit(" · ".join(parts))


def match_session(word, names, prefixes=()):
    """Il nome dettato (o un suo prefisso, senza il prefisso d'account): [candidati]. Esatto vince da solo."""
    w = (word or "").strip().lower()
    if not w:
        return []
    shorts = {n: short_name(n, prefixes, n=999).lower() for n in names}
    exact = [n for n, sn in shorts.items() if sn == w or n.lower() == w]
    if len(exact) == 1:
        return exact
    return [n for n, sn in shorts.items() if sn.startswith(w) or n.lower().startswith(w)]


# ------------------------------------------------------------------ tastiere
# Regola definitiva (l'utente 18:24, 21:00; nomi del 12/09/2026 09:26): ogni bottone di UNA parola sta da solo
# sulla sua riga; tasti CONTESTUALI, mai piu' di quattro in fondo, mai un tasto che ripete quello che hai
# davanti, «Avvia master» solo se la master non e' viva, il piu' probabile per primo. Verbi all'imperativo per
# le azioni (Avvisami, Continua, Ferma, Leggi tutto, Invia di nuovo, Annulla modifiche), sostantivi per le
# destinazioni (Sessioni, Terminale, «◀ nome»).
def _row(text, cb):
    return [{"text": fit(text, WIDTH), "callback_data": cb}]


def keyboard_back(name="", label=""):
    """«◀ nome» (la scheda da cui vieni, se c'e') e Sessioni: sotto quota, aiuto, Leggi tutto, conferme, errori."""
    rows = [_row(f"◀ {label or name}", f"card:{name}")] if name else []
    return {"inline_keyboard": rows + [_row("Sessioni", "list")]}


keyboard_fixed = keyboard_back   # nome storico


def keyboard_list(labels, master_alive=True):
    """Elenco: un bottone per sessione, poi Recap, Quota e — solo se manca — Avvia master."""
    rows = [_row(lab, f"n:{i}") for i, lab in enumerate(labels, 1)] + [_row("Recap", "recap"), _row("Quota", "quota")]
    if not master_alive:
        rows.append(_row("Avvia master", "master"))
    return {"inline_keyboard": rows}


def keyboard_card(options, following=False, state="idle", has_checkpoint=False, full_question=False, link="", link_mode="app"):
    """Scheda: le opzioni della domanda, Avvisami/Basta avvisi, Continua (solo su ✓ ferma: a una che lavora o che
    chiede non si dice «continua»), Riprendi su ✗ sparita (la rilancia, 15/09), Annulla modifiche (solo se c'e' un
    checkpoint), Sessioni."""
    rows = [_row(f"{i + 1} {o}", f"opt:{i + 1}") for i, o in enumerate(options)]
    if full_question:
        rows.append(_row("Domanda intera", "q:"))
    rows.append(_row("Basta avvisi" if following else "Avvisami", "follow"))
    if state == "idle":
        rows.append(_row("Continua", "resume"))
    elif state == "dead":
        rows.append(_row("Riprendi", "reopen"))
    if has_checkpoint:
        rows.append(_row("Annulla modifiche", "rollback"))
    lb = link_button(link, link_mode)
    if lb and state != "dead":
        rows.append(lb)
    rows.append(_row("Sessioni", "list"))
    return {"inline_keyboard": rows}


def keyboard_live(name, label=""):
    """Il messaggio VIVO di un prompt dal watch (si aggiorna da solo mentre la sessione lavora):
    Ferma (Esc), Terminale (le ultime righe dello schermo), Sessioni."""
    return {"inline_keyboard": [_row("Ferma", f"stop:{name}"), _row("Terminale", f"screen:{name}"), _row("Sessioni", "list")]}


keyboard_after_send = lambda name, label="", following=True: keyboard_live(name, label)   # noqa: E731  nome storico


def keyboard_retry(name, label=""):
    """«⚠ nome non ha ricevuto»: Invia di nuovo (lo stesso prompt, con --force), Sessioni."""
    return {"inline_keyboard": [_row("Invia di nuovo", f"retry:{name}"), _row("Sessioni", "list")]}


def intent_url(link):
    """Il link come intent Android per Chrome (l'utente via master 12/09: l'app Claude tiene un solo login,
    l'altro account vive in Chrome): intent://host/path#Intent;scheme=https;package=com.android.chrome;end"""
    l = str(link or "")
    m = re.match(r"^https?://(.+)$", l)
    return f"intent://{m.group(1)}#Intent;scheme=https;package=com.android.chrome;end" if m else l


def link_button(link, mode="app"):
    """La riga-bottone che apre la sessione nel posto giusto per il suo account: «Apri sessione» (URL https,
    l'app Claude lo prende) o «Apri in Chrome» (intent Chrome; se Telegram lo rifiuta, reply() ripiega sul
    link nel testo). None senza link."""
    if not link:
        return None
    if mode == "browser":
        return [{"text": "Apri in Chrome", "url": intent_url(link)}]
    return [{"text": "Apri sessione", "url": str(link)}]


def keyboard_notice(name, options, label="", full_question=False, link="", link_mode="app"):
    """L'avviso di una domanda (dall'hook): al massimo TRE bottoni (via master 12/09: Wear OS ne mostra pochi):
    le opzioni se sono ≤ 3, altrimenti le prime due; «Domanda intera» (q:) solo se la sintesi ha tagliato
    qualcosa (via master 12/09 11:38); poi «Apri <icona> nome» (la scheda ha tutte le opzioni)."""
    shown = list(options) if len(options) <= 3 else list(options)[:2]
    rows = [_row(f"{i + 1} {o}", f"ans:{name}:{i + 1}") for i, o in enumerate(shown)]
    if full_question:
        rows.append(_row("Domanda intera", f"q:{name}"))
    rows.append(_row(f"Apri {label or name}", f"card:{name}"))
    lb = link_button(link, link_mode)
    if lb:
        rows.append(lb)
    return {"inline_keyboard": rows}


def keyboard_outcome(name, label="", cut=False):
    """L'esito o la risposta di una sessione: Leggi tutto (solo se tagliato), «Apri <icona> nome», Basta avvisi."""
    rows = [_row("Leggi tutto", f"full:{name}")] if cut else []
    return {"inline_keyboard": rows + [_row(f"Apri {label or name}", f"card:{name}"), _row("Basta avvisi", f"unfollow:{name}")]}


def keyboard_start(master_alive=False):
    """/start: Avvia master (se manca) e Sessioni."""
    return {"inline_keyboard": ([] if master_alive else [_row("Avvia master", "master")]) + [_row("Sessioni", "list")]}


def keyboard_digest(waiting, label_of=None):
    """Digest delle 8:00: «Apri <nome>» per ogni ❓ (al massimo quattro), poi Sessioni."""
    rows = [_row(f"Apri {(label_of or (lambda n: n))(n)}", f"card:{n}") for n in waiting[:4]]
    return {"inline_keyboard": rows + [_row("Sessioni", "list")]}


def keyboard_confirm():
    return keyboard_back()


# ------------------------------------------------------------------ messaggio vivo (transcript)
TOOL_INPUT_KEYS = ("command", "file_path", "pattern", "path", "query", "url", "prompt", "description")


def tool_note(tool_input):
    """La `description` che Claude scrive accanto a un comando Bash: dice l'INTENTO («Run the plugin test suite»)
    dove il comando dice solo «cd …» (contratto 1.5, chiesto dal polso il 14/09). "" se non c'e'."""
    if isinstance(tool_input, dict):
        d = " ".join(str(tool_input.get("description") or "").split())
        return d[:120]
    return ""


def tool_line(name, tool_input, width=120):
    """«Bash git log --since yesterday»: il tool e l'input (fino a `width` caratteri, senza segno di taglio), come
    lo spinner del desktop."""
    detail = ""
    if isinstance(tool_input, dict):
        for k in TOOL_INPUT_KEYS:
            if tool_input.get(k):
                detail = " ".join(str(tool_input[k]).split())
                break
    elif tool_input:
        detail = " ".join(str(tool_input).split())
    return line(f"{name} {detail[:width]}")


def transcript_events(path, offset):
    """Gli eventi del transcript (jsonl di Claude Code) scritti dopo `offset` byte: ("user", testo),
    ("text", testo assistant), ("tool", nome, input). Torna (eventi, nuovo offset). Righe rotte ignorate."""
    events = []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return events, offset
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        kind = d.get("type")
        content = (d.get("message") or {}).get("content")
        if kind == "user":
            if isinstance(content, str):
                events.append(("user", content))
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        events.append(("user", b["text"]))
        elif kind == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    events.append(("text", b["text"]))
                elif b.get("type") == "tool_use":
                    events.append(("tool", str(b.get("name") or "?"), b.get("input")))
    return events, offset + len(data)


def live_lines(label, since, tool="", note="", width=WIDTH, max_lines=4):
    """Il messaggio vivo a larghezza piena: «▶ nome · 2m · Bash pytest -q tests» in UNA riga, poi l'ultimo testo intero."""
    head = join(f"▶ {label}", age_compact(since) if since >= 60 else "al lavoro", tool)
    lines = [head]
    if note:
        lines.append(line(strip_markdown(note)))
    return lines[:max_lines]


def question_gist(text, max_chars=88):
    """La domanda in breve, deterministica (l'utente 12/09 10:54: tagliata non si puo' rispondere): tutta se sta in
    max_chars; altrimenti l'ULTIMA frase interrogativa che ci sta (il contesto prima si lascia); altrimenti ""
    (serve una sintesi: la fa cm-answer col modello)."""
    t = " ".join(str(text or "").split())
    if len(t) <= max_chars:
        return t
    for s in reversed(re.findall(r"[^.!?]*\?", t)):
        s = s.strip()
        if s and len(s) <= max_chars:
            return s
    return ""


def voice_split(text, n=120):
    """(riga vocale, resto): la riga «Esito:» per la lettura vocale (Handwave, via master 12/09) e' UNA frase,
    ≤ n caratteri, senza segni di elenco ne' percorsi (di un percorso resta l'ultimo pezzo); quel che segue la
    prima frase non si perde, torna come `resto`."""
    t = " ".join(str(text or "").split())
    t = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", t)
    t = re.sub(r"(?<![\w.])/(?:[\w.\-]+/)*([\w.\-]+)", r"\1", t)   # /home/x/y.py → y.py
    rest = ""
    m = re.search(r"[.!?](?=\s|$)", t)
    if m:
        t, rest = t[:m.end()], t[m.end():].strip()
    if len(t) > n:
        t, rest = t[:n - 1].rstrip() + "…", (t[n - 1:].strip() + " " + rest).strip()
    return t, rest


def voice_line(text, n=120):
    return voice_split(text, n)[0]


def watch_line(text):
    """La riga «Watch: …» (l'ultima) del messaggio di una sessione, senza marcatore; "" se manca."""
    for l in reversed(str(text or "").splitlines()):
        m = re.match(r"^\s*\W*\s*watch\s*:\s*(.+)$", l, flags=re.I)
        if m:
            return strip_markdown(m.group(1)).strip()
    return ""

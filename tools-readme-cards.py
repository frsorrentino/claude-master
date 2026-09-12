#!/usr/bin/env python3
"""Genera le card del README (assets/readme/*.svg + .png, 1200×630) — zero modelli, solo cairosvg.
Stile: le card di fable-director (gradiente scuro, titolo 52 bold ≤ 6 parole, sottotitolo 26, UI finta
ma fedele dentro). Dataset DEMO neutro in ogni card (repo pubblico: mai un nome di cliente):
master 🔴 ▶ · atlas-shop 🟩 ▶ · field-notes 🟠 ✓ 2d · ledger-api 🟦 ❓ «Deploy now?» · orbit-docs 🟣 ✗. Una card, un messaggio."""
import cairosvg
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "assets" / "readme"
FONT = "font-family=\"'DejaVu Sans',Helvetica,Arial,sans-serif\""   # DejaVu ha ▶ ✓ ✗: e' cio' che cairosvg rende qui
MONO = "'DejaVu Sans Mono',Menlo,monospace"
GREEN, YEL, RED, BLUE, PURP, ORANGE = "#3ddc84", "#ffd43b", "#ff6b6b", "#5aa7ff", "#a78bfa", "#ff8c42"
INK, DIM, LINE, PANEL = "#e8eefc", "#9aa7c7", "#2f3f63", "#0b1020"
# il dataset demo: (nome, forma, colore, stato)
DEMO = [("master", "circle", "#ff4d4d", "busy"), ("atlas-shop", "square", GREEN, "busy"), ("field-notes", "circle", ORANGE, "idle"),
        ("ledger-api", "square", BLUE, "waiting"), ("orbit-docs", "circle", PURP, "dead")]
STATE_GLYPH = {"busy": "▶", "idle": "✓", "waiting": "?", "dead": "✗"}
STATE_COL = {"busy": YEL, "idle": GREEN, "waiting": RED, "dead": "#8a94b3"}


def head(bg1, bg2):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" {FONT} xml:space="preserve">
  <defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/></linearGradient>
  <marker id="m" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L7,3.5 L0,7 z" fill="{GREEN}"/></marker>
  <clipPath id="round"><circle cx="0" cy="0" r="132"/></clipPath></defs>
  <rect width="1200" height="630" fill="url(#bg)"/>'''


def title(t, sub, y=100, size=52):
    return label(70, y, t, size, "#ffffff", "800") + label(70, y + 46, sub, 26, "#b9c7e8")


WARN = []
CURRENT = [""]


def label(x, y, text, size=22, color=INK, weight="500", mono=False, anchor="start"):
    ff = f' font-family="{MONO}"' if mono else ""
    # stima della larghezza (DejaVu ≈ 0,6 em per carattere, il mono 0,62): chi sfora il bordo viene segnalato
    est = len(text) * size * (0.62 if mono else (0.6 if weight in ("700", "800") else 0.56))
    right = x + est if anchor == "start" else (x + est / 2 if anchor == "middle" else x)
    if right > 1150:
        WARN.append(f"{CURRENT[0]}: «{text[:50]}…» size {size} → ~{int(right)} px")
    return f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}"{ff} text-anchor="{anchor}">{text}</text>'


def icon(x, y, shape, color, r=9):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>' if shape == "circle" else f'<rect x="{x - r}" y="{y - r}" width="{2 * r}" height="{2 * r}" rx="3" fill="{color}"/>'


def panel(x, y, w, h, stroke=LINE, fill=PANEL):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}"/>'


def button(x, y, w, text, h=34, fill="#1e2a44", stroke="#3b4d78", size=17, color=INK):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}"/>' + label(x + w / 2, y + h / 2 + 6, text, size, color, "600", anchor="middle")


def foot(text):
    return label(70, 592, text, 20, DIM)


arrow = f'stroke="{GREEN}" stroke-width="3" marker-end="url(#m)"'


def session_row(x, y, name, shape, color, state, extra="", size=19):
    g = STATE_GLYPH[state]
    return (label(x, y, g, size, STATE_COL[state], "800") + icon(x + 32, y - 7, shape, color, 8)
            + label(x + 50, y, name, size) + (label(x + 50 + 11 * len(name) + 14, y, extra, size - 2, DIM) if extra else ""))


def watch_round(cx, cy, r=132):
    """Cornice tonda (Pixel Watch): cassa, lunetta, vetro scuro; il contenuto va dentro clip «round»."""
    return (f'<circle cx="{cx}" cy="{cy}" r="{r + 26}" fill="#2a2f3a"/><circle cx="{cx}" cy="{cy}" r="{r + 16}" fill="#0f131c"/>'
            f'<rect x="{cx + r + 22}" y="{cy - 22}" width="12" height="44" rx="4" fill="#3a404d"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#05070c"/>')


def apple_case(x, y, D):
    """Apple Watch a misure reali (Series 10, 46 mm) rapportate al Pixel Watch di diametro di cassa D:
    cassa alta D e larga 0,85·D con angoli ~20 % (r ≈ 9 mm su 46), schermo verticale 1,19 centrato con
    bordo ≈ 2,5 mm (17 px su 316), angoli dello schermo ≈ 20 % della larghezza; Digital Crown e tasto a
    destra. Torna (svg, sx, sy, sw, sh) dello schermo."""
    W, H = 0.85 * D, D
    bezel = D * 2.5 / 46
    sh = H - 2 * bezel
    sw = sh / 1.19
    if sw > W - 2 * bezel:
        sw = W - 2 * bezel; sh = sw * 1.19
    sx, sy = x + (W - sw) / 2, y + (H - sh) / 2
    o = (f'<rect x="{x}" y="{y}" width="{W}" height="{H}" rx="{0.2 * H}" fill="#2a2f3a"/>'
         f'<rect x="{x + 4}" y="{y + 4}" width="{W - 8}" height="{H - 8}" rx="{0.2 * H - 4}" fill="#0f131c"/>'
         f'<rect x="{x + W - 2}" y="{y + 0.22 * H}" width="12" height="{0.16 * H}" rx="5" fill="#3a404d"/>'
         f'<rect x="{x + W - 1}" y="{y + 0.44 * H}" width="8" height="{0.2 * H}" rx="3" fill="#3a404d"/>'
         f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="{0.2 * sw}" fill="#05070c"/>')
    return o, sx, sy, sw, sh


def screen_round(c):
    """Il contenuto del bot dentro il tondo (Pixel Watch): tastiere inline vere, come misurato."""
    o = ""
    y = -96
    if c.get("title"):
        o += label(0, y + 8, c["title"], 14, INK, "800", anchor="middle"); y += 26
    for n, sh, col, st in c.get("sessions", []):
        o += bot_button(-84, y, 168, n, STATE_GLYPH[st], STATE_COL[st], sh, col, 24); y += 28
    for l in c.get("lines", []):
        o += label(0, y + 12, l, 13, INK, anchor="middle"); y += 18
    if c.get("question"):
        o += label(0, y + 14, c["question"], 14, INK, "700", anchor="middle"); y += 22
    for i, opt in enumerate(c.get("options", [])):
        o += bot_button(-84, y, 168, f"{i + 1} {opt}", h=24); y += 28
    if c.get("footer"):
        o += bot_button(-84, y, 168, c["footer"], h=22)
    return o


def screen_rect(c, w=235, h=280):
    """Lo stesso contenuto nell'Apple Watch: solo testo, stesso corpo del tondo — le tastiere inline su
    watchOS non sono verificate, le opzioni sono righe; l'altezza in piu' vale una o due righe."""
    o = label(w / 2, 24, "Claude Master", 12, DIM, anchor="middle")
    y = 50
    if c.get("title"):
        o += label(16, y, c["title"], 14, INK, "800"); y += 26
    for n, sh, col, st in c.get("sessions", []):
        o += label(16, y, STATE_GLYPH[st], 14, STATE_COL[st], "800") + icon(40, y - 5, sh, col, 6) + label(54, y, n, 14, INK, "600"); y += 26
    for l in c.get("lines", []):
        o += label(16, y, l, 13, INK); y += 20
    if c.get("question"):
        o += label(16, y, c["question"], 14, INK, "700"); y += 24
    for i, opt in enumerate(c.get("options", [])):
        o += label(16, y, f"{i + 1} {opt}", 14, INK); y += 22
    if c.get("footer"):
        o += label(16, min(y + 6, h - 14), c["footer"], 13, "#9ad1ff", "700")
    return o


def watch_pair(x, y, content, content_rect=None, scale=1.0, captions=("Wear OS", "iOS")):
    """REGOLA (Franz 19:53, 19:57): dove c'e' un orologio ci sono ENTRAMBI, affiancati, con lo STESSO
    contenuto del bot: tondo (Pixel Watch, con i bottoni misurati su Wear OS) e Apple Watch a proporzioni
    reali (solo testo: le tastiere inline su watchOS non sono verificate). `content_rect` e' ignorato.
    (x, y) e' l'angolo in alto a sinistra della coppia; larghezza ≈ 630·scale, altezza ≈ 350·scale."""
    r = 132
    D = 2 * (r + 26)
    cx, cy = x + D / 2, y + D / 2
    o = f'<g transform="translate({x},{y}) scale({scale}) translate({-x},{-y})">'
    o += watch_round(cx, cy, r) + f'<g transform="translate({cx},{cy})">' + screen_round(content) + "</g>"
    ax = x + D + 44
    case, sx, sy, sw, sh = apple_case(ax, y, D)
    o += case + f'<g transform="translate({sx},{sy})">' + screen_rect(content, sw, sh) + "</g>"
    o += label(cx, y + D + 24, captions[0], 16, DIM, anchor="middle") + label(ax + 0.425 * D, y + D + 24, captions[1] + " · illustration", 16, DIM, anchor="middle")
    return o + "</g>"


def phone_frame(x, y, lines, scale=1.0, w=320, clip_h=None):
    """Uno smartphone con la chat Telegram del bot, fedele allo scatto vero ma col dataset demo: barra di
    stato, intestazione (freccia, avatar tondo, «Claude / bot», tre punti), barra «Messaggi non letti», UNA
    bolla col recap (nome della sessione in blu grassetto dopo l'icona, frase troncata con «…»), barra con
    «Menu», «Messaggio» e il microfono. `lines` = [(testo, stile)]: "h" titolo, "sec" sezione, "sess:<shape>:
    <color>:<nome>" voce con nome, "t" testo, "ok" riga ✓. `clip_h` ritaglia il telefono in basso, come in
    uno scatto scorrevole. Larghezza reale w·scale."""
    BLUE_LINK, PANEL_TG, BG_TG = "#5aa7ff", "#1c2733", "#0e1621"
    o = f'<g transform="translate({x},{y}) scale({scale})">'
    if clip_h:
        o = f'<clipPath id="ph{x}{y}"><rect x="{x - 14}" y="{y - 14}" width="{w * scale + 28}" height="{clip_h}"/></clipPath><g clip-path="url(#ph{x}{y})">' + o
    H = 760
    o += f'<rect x="-14" y="-14" width="{w + 28}" height="{H + 28}" rx="40" fill="#2a2f3a"/><rect x="0" y="0" width="{w}" height="{H}" rx="30" fill="{BG_TG}"/>'
    # barra di stato
    o += label(22, 30, "20:04", 15, INK, "700")
    for i, hh in enumerate((4, 7, 10, 13)):
        o += f'<rect x="{w - 96 + i * 5}" y="{30 - hh}" width="3.5" height="{hh}" rx="1" fill="{INK}"/>'
    o += f'<path d="M{w - 66},27 q10,-11 20,0" stroke="{INK}" stroke-width="2.5" fill="none"/><circle cx="{w - 56}" cy="29" r="1.6" fill="{INK}"/>'
    o += f'<rect x="{w - 40}" y="19" width="26" height="13" rx="4" fill="#ffffff"/>' + label(w - 27, 30, "80", 10, "#0e1621", "800", anchor="middle")
    # intestazione
    o += f'<rect x="10" y="46" width="34" height="34" rx="12" fill="{PANEL_TG}"/>' + label(27, 69, "←", 17, INK, "700", anchor="middle")
    o += f'<rect x="52" y="44" width="{w - 106}" height="40" rx="14" fill="{PANEL_TG}"/><circle cx="76" cy="64" r="16" fill="{ORANGE}"/>'
    for k in range(8):
        import math
        o += f'<line x1="76" y1="64" x2="{76 + 11 * math.cos(k * math.pi / 4):.1f}" y2="{64 + 11 * math.sin(k * math.pi / 4):.1f}" stroke="#ffffff" stroke-width="2.4"/>'
    o += label(102, 61, "Claude", 16, "#ffffff", "700") + label(102, 77, "bot", 12, DIM)
    o += f'<rect x="{w - 44}" y="46" width="34" height="34" rx="12" fill="{PANEL_TG}"/>' + label(w - 27, 69, "⋮", 16, INK, "700", anchor="middle")
    # messaggi non letti
    o += f'<rect x="0" y="92" width="{w}" height="28" fill="#152230"/>' + label(w / 2, 111, "Unread messages", 12, INK, anchor="middle") + label(w - 16, 111, "⌄", 12, DIM, anchor="middle")
    # bolla
    top = 132
    yy = top + 26
    body = ""
    for text, style in lines:
        if style == "h":
            body += label(18, yy, text, 14, "#ffffff", "700"); yy += 20
        elif style == "sec":
            body += label(18, yy, text, 13, INK); yy += 20
        elif style.startswith("sess:"):
            _, shape, color, name = style.split(":", 3)
            body += icon(26, yy - 5, shape, color, 7) + label(38, yy, name, 13, BLUE_LINK, "700") + label(38 + 8.7 * len(name), yy, text, 13, INK); yy += 18
        elif style == "ok":
            body += label(18, yy, text, 13, INK); yy += 18
        elif style == "gap":
            yy += 10
        else:
            body += label(18, yy, text, 13, INK); yy += 18
    o += f'<rect x="10" y="{top}" width="{w - 20}" height="{yy - top + 6}" rx="16" fill="#182533"/>' + body
    # barra in basso
    by = H - 54
    o += f'<rect x="10" y="{by}" width="76" height="36" rx="18" fill="{BLUE}"/>' + label(48, by + 24, "≡ Menu", 13, "#ffffff", "700", anchor="middle")
    o += f'<circle cx="104" cy="{by + 18}" r="10" fill="none" stroke="{DIM}" stroke-width="1.5"/>' + label(122, by + 24, "Message", 14, DIM)
    o += label(w - 60, by + 25, "⊘", 14, DIM, anchor="middle") + f'<circle cx="{w - 28}" cy="{by + 18}" r="16" fill="{BLUE}"/>' + label(w - 28, by + 24, "●", 12, "#ffffff", anchor="middle")
    o += "</g>"
    return o + ("</g>" if clip_h else "")


def bot_button(x, y, w, text, glyph=None, gcolor=None, shape=None, icolor=None, h=30):
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="#19233b" stroke="#324472"/>'
    tx = x + 10
    if glyph:
        o += label(tx, y + 21, glyph, 15, gcolor or INK, "800"); tx += 20
    if shape:
        o += icon(tx + 6, y + 15, shape, icolor, 6); tx += 20
    return o + label(tx, y + 21, text, 15, INK, "600")


cards = {}

# ---------------------------------------------------------------- hero
CURRENT[0] = "card0-hero"
s = head("#151a33", "#0a0e1c") + title("One master runs all the others.", "Parallel sessions, one per project — terminal and wrist")
s += panel(70, 200, 360, 330, "#3b4d78", "#101733") + icon(108, 232, "circle", "#ff4d4d", 11) + label(130, 240, "master", 26, "#ffffff", "800")
s += label(96, 272, "the session of the root folder:", 18, DIM) + label(96, 296, "launches, watches, answers,", 18, DIM) + label(96, 320, "closes the rest", 18, DIM)
s += label(96, 372, "$ claude-master sessions", 19, "#9ad1ff", "700", True) + label(96, 400, '$ claude-master talk ledger-api "…"', 19, "#9ad1ff", "700", True)
s += label(96, 428, "$ claude-master answer ledger-api 1", 19, "#9ad1ff", "700", True) + label(96, 456, "$ claude-master restore", 19, "#9ad1ff", "700", True)
s += label(96, 500, "your phone talks to it, too", 18, DIM)
for i, (n, sh, col, st) in enumerate(DEMO[1:]):
    y = 218 + i * 80
    s += panel(500, y, 260, 64, LINE) + icon(530, y + 32, sh, col, 12) + label(554, y + 39, n, 22, "#ffffff", "700")
    s += label(732, y + 39, STATE_GLYPH[st], 22, STATE_COL[st], "800", anchor="end")
    s += f'<line x1="438" y1="{y + 32}" x2="492" y2="{y + 32}" {arrow}/>'
s += watch_pair(790, 205, {"title": "5 sessions · 1? · 1✗", "sessions": DEMO, "footer": "List"}, scale=0.56)
s += foot("launch, list, talk, answer, restart, restore, tile — tmux underneath, Telegram on top")
cards["card0-hero"] = s + "</svg>"

# ---------------------------------------------------------------- 1 sessions
CURRENT[0] = "card1-sessions"
s = head("#151a33", "#0d1120") + title("Sessions at a glance.", "Every account: state, who waits for you, how to reach it")
s += panel(70, 190, 1060, 330)
s += label(96, 232, "$ claude-master sessions", 22, "#9ad1ff", "700", True)
s += label(96, 272, "ACCOUNT        NAME          STATE      VIEW       CHANNEL   SINCE", 19, "#7f8db0", "500", True)
rows = [("personal", "master", "busy", "open", "(this)", "3h14m"), ("work", "atlas-shop", "busy", "open", "native", "41m"), ("personal", "field-notes", "idle", "open", "native", "2d"),
        ("work", "ledger-api", "waiting", "DETACHED", "talk", "12m"), ("personal", "orbit-docs", "?", "-", "talk", "-")]
for i, (a, n, st, v, ch, t) in enumerate(rows):
    y = 308 + i * 32
    col = {"busy": YEL, "idle": GREEN, "waiting": RED, "?": "#8a94b3"}[st]
    s += label(96, y, f"{a:<14} {n:<13} ", 19, INK, "500", True) + label(96 + 11.45 * 29, y, f"{st:<10}", 19, col, "700", True) + label(96 + 11.45 * 40, y, f"{v:<10} {ch:<9} {t}", 19, INK, "500", True)
s += label(96, 480, "  ← ledger-api is waiting for an answer and nobody is watching it", 19, RED, "600", True)
s += foot("a detached session stuck on a question is work standing still: it goes first")
cards["card1-sessions"] = s + "</svg>"

# ---------------------------------------------------------------- 2 launch
CURRENT[0] = "card2-launch"
s = head("#0f2a2a", "#08161a") + title("Launch by a piece of a name.", "Folder resolved, account deduced, a tab in your Terminal")
s += label(70, 220, '$ claude-master launch atlas', 26, "#9ad1ff", "700", True)
steps = [("atlas", "→ ~/work/clients/atlas-shop  (one match under the root)"), ("account", "→ work  (deduced from the folder, a warning never a refusal)"),
         ("window", "→ a tab in the open Terminal window, no start tab left beside it"), ("link", "→ https://claude.ai/code/session_…  (the same session on your phone)")]
for i, (k, v) in enumerate(steps):
    y = 285 + i * 52
    s += label(96, y, k, 22, "#b9e8d0", "700", True) + label(230, y, v, 20, INK, "500", True)
s += panel(70, 500, 1060, 60, "#2f6b55", "#0b1a1a") + label(96, 538, "a typo never becomes a folder: --create only when you ask for it, with the full path shown first", 19, "#b9e8d0")
s += foot("trust and bypass dialogs answered for you; startup confirmed, tab verified attached")
cards["card2-launch"] = s + "</svg>"

# ---------------------------------------------------------------- 3 talk & answer
CURRENT[0] = "card3-talk"
s = head("#0f2a2a", "#08161a") + title("Talk. Answer its question.", "From another session, from your phone, on either account")
s += panel(70, 200, 580, 160, "#2f6b55") + label(96, 240, '$ claude-master talk ledger-api "deploy done?"', 19, "#9ad1ff", "700", True)
s += label(96, 280, "delivered to ledger-api's own inbox; the answer is read", 19, INK) + label(96, 306, "back from its transcript", 19, INK) + label(96, 340, "→ «yes, live since 14:02, cache purged»", 19, GREEN, "600")
s += panel(70, 390, 580, 160, "#2f6b55") + label(96, 430, "$ claude-master answer ledger-api --show", 19, "#9ad1ff", "700", True)
s += label(96, 462, "«ledger-api» asks — Deploy: deploy now?", 19, INK) + label(96, 488, "  ❯ 1. yes", 19, INK, "500", True) + label(96, 512, "    2. no", 19, INK, "500", True)
s += label(96, 540, "$ claude-master answer ledger-api 1   → answered 1. yes", 19, "#9ad1ff", "700", True)
s += watch_pair(690, 200, {"title": "ledger-api", "lines": ["? waiting for you", "Deploy ready, waiting", "for the client's ok", "→ Wait for the go"], "question": "Deploy now?", "options": ["yes", "no"], "footer": "List"}, scale=0.74)
s += label(920, 510, "an inbox message never unblocks a dialog: answer does", 16, DIM, anchor="middle")
s += foot("same registry → native channel; otherwise the tmux pane, after checking for typed text")
cards["card3-talk"] = s + "</svg>"

# ---------------------------------------------------------------- 4 reboot
CURRENT[0] = "card4-reboot"
s = head("#2a1a10", "#160d08") + title("Survive a reboot with everything.", "Registry + last good set: restore proposes all, with a date")
s += panel(70, 200, 470, 320, "#4a3320") + label(96, 242, "before the reboot", 22, "#ffb27a", "700")
for i, (n, sh, col, st) in enumerate(DEMO):
    s += icon(112, 285 + i * 34 - 7, sh, col, 9) + label(132, 285 + i * 34, n, 21)
s += label(96, 470, "two windows closed by hand → the registry", 17, DIM) + label(96, 494, "shrank to 3; the last good set kept 5", 17, DIM)
s += f'<line x1="560" y1="360" x2="640" y2="360" {arrow}/>'
s += panel(660, 200, 470, 320, "#4a3320") + label(686, 242, "the first shell asks", 22, "#ffb27a", "700")
s += label(686, 280, "to restore (5), registry of 09:51:", 19, INK, "500", True)
for i, (n, src) in enumerate([("master", "registry 09:51"), ("atlas-shop", "registry 09:51"), ("field-notes", "registry 09:51"), ("ledger-api", "snapshot 09:40"), ("orbit-docs", "snapshot 09:12")]):
    s += label(686, 316 + i * 28, f"{n:<12} {src}", 18, INK if "registry" in src else "#ffb27a", "500", True)
s += label(686, 470, "restore? [Y/n]", 19, INK, "700", True) + label(686, 498, "ok: all five back, each with its conversation", 16, GREEN, "500", True)
s += foot("each one restarts with --continue; only an explicit close shrinks the last good set")
cards["card4-reboot"] = s + "</svg>"

# ---------------------------------------------------------------- 5 restart
CURRENT[0] = "card5-restart"
s = head("#1a1033", "#0c0818") + title("Restart in place, keep talking.", "Hooks and plugins load at startup: arm, finish, come back")
s += panel(70, 200, 500, 330, "#4b3a7a") + label(96, 240, "$ claude-master restart arm", 22, "#9ad1ff", "700", True)
s += label(96, 280, "restart armed: tmux «atlas-shop» (pid 4812)", 18, INK, "500", True) + label(96, 306, "  fires at the end of this turn", 18, DIM, "500", True)
s += label(96, 350, "one flag per session: eight sessions", 18, INK) + label(96, 374, "can arm in the same minute", 18, INK)
s += label(96, 418, "$ claude-master restart list", 22, "#9ad1ff", "700", True)
for i, (n, t) in enumerate([("atlas-shop", "13:10 --clean"), ("ledger-api", "13:10"), ("field-notes", "13:11 --switch-account work")]):
    s += label(96, 452 + i * 26, f"{n:<12} {t}", 17, INK, "500", True)
s += panel(630, 200, 500, 330, "#4b3a7a") + label(656, 240, "the Stop hook, at the end of the turn", 20, "#c9b8ff", "700")
for i, t in enumerate(["1  /exit to the session (Esc first)", "2  wait, then TERM, then KILL — never mid-turn", "3  free the tmux name", "4  launch again with --continue", "5  a new session id: a new entry in the app"]):
    s += label(656, 288 + i * 32, t, 18)
s += label(656, 470, "--clean: fresh context, memory intact", 17, DIM) + label(656, 496, "--switch-account: same transcript, the other org", 17, DIM)
s += foot("a kill from inside a tool lands mid-turn: the Stop hook is the only safe instant")
cards["card5-restart"] = s + "</svg>"

# ---------------------------------------------------------------- 6 screen
CURRENT[0] = "card6-screen"
s = head("#151a33", "#0d1120") + title("Your screen, arranged.", "tile, merge, move, layout — app windows, no start tab")
s += '<rect x="70" y="200" width="620" height="330" rx="10" fill="#0b1020" stroke="#3b4d78"/>'
s += '<rect x="80" y="210" width="360" height="310" rx="6" fill="#141c30" stroke="#324472"/>' + icon(100, 226, "circle", "#ff4d4d", 7) + label(114, 231, "master", 15, INK, "700")
for i, (n, sh, col, st) in enumerate(DEMO[1:]):
    y = 210 + i * 78
    s += f'<rect x="450" y="{y}" width="230" height="70" rx="6" fill="#141c30" stroke="#324472"/>' + icon(468, y + 16, sh, col, 6) + label(480, y + 21, n, 14, INK, "700")
s += label(70, 555, "$ claude-master tile   → 5 windows: master 60 % left, four stacked right", 18, "#9ad1ff", "700", True)
s += panel(720, 200, 410, 330, LINE) + label(746, 240, "tile / merge / move / layout", 20, "#ffffff", "700")
for i, t in enumerate(["tile: one app window per session", "merge: all of them as tabs of one window", "move destra: to another monitor", "layout save|restore mattina", "no start tab beside a session", "odd count + master: master-primary"]):
    s += label(746, 282 + i * 34, t, 18)
s += foot("ChromeOS Terminal via chrome-bridge: tabs never move, so tile opens and merge duplicates")
cards["card6-screen"] = s + "</svg>"

# ---------------------------------------------------------------- 7a wrist (Wear OS)
CURRENT[0] = "card7-wrist"
s = head("#0f2a2a", "#08161a") + title("From your wrist.", "Telegram, 22 characters wide: tap, dictate, be alerted")
for i, t in enumerate(["«sessioni» → one button per session", "tap a name → its card: state, outcome, next", "a question → its options as buttons", "a number, «due si» or a tap answers it", "free text → a prompt, Invia / Annulla", "silent, except questions and outcomes"]):
    s += label(70, 220 + i * 40, "•  " + t, 20, INK)
s += label(70, 480, "«sessioni», «lancia ledger-api», «segui», «riprendi»", 18, "#9ad1ff", "700", True) + label(70, 508, "bare words, no slash: dictation-friendly", 17, DIM)
s += watch_pair(600, 190, {"title": "5 sessions · 1? · 1✗", "sessions": DEMO, "footer": "List"}, scale=0.92)
s += foot("Wear OS tested: keyboards render, taps arrive · watchOS: notifications and dictation, not verified")
cards["card7-wrist"] = s + "</svg>"

# ---------------------------------------------------------------- 8 guard & night
s = head("#2a1a10", "#160d08") + title("Guard, diary, digest, night.", "Warned at 95 %, resumed at reset; 20:00, 08:00 and 02:00")
recap = [("Recap 11/09/2026 · 5 projects ·", "h"), ("1 waiting on a question", "h"), ("", "gap"), ("WAITING ON A QUESTION", "sec"),
         (" · AskUserQuestion", "sess:square:" + BLUE + ":ledger-api"), ("     Deploy now?", "t"), ("", "gap"), ("OPEN", "sec"),
         (": Watch pairs on every card,", "sess:circle:#ff4d4d:master"), ("README rebuilt around them…", "t"), ("", "gap"),
         (": Staging deploy done,", "sess:square:" + GREEN + ":atlas-shop"), ("cache purged, waiting for…", "t"), ("", "gap"),
         (": Three drafts ready,", "sess:circle:" + ORANGE + ":field-notes"), ("waiting for the client's read.", "t"), ("", "gap"),
         ("CLOSED TODAY", "sec"), ("✓ orbit-docs: Changelog rewritten, PDF", "ok"), ("regenerated (6 pages), quotes…", "t")]
s += phone_frame(70, 188, recap, scale=0.86, w=320, clip_h=386)
s += panel(400, 190, 730, 160, "#4a3320") + label(426, 228, "guard, every 5 minutes", 20, "#ffb27a", "700")
s += label(426, 264, "⚠ quota «work» at 96 % (5 h): reset at 16:00", 17, INK, "500", True) + label(426, 292, "✓ quota «work» back: resumed 2 sessions", 17, GREEN, "500", True)
s += label(426, 330, "one warning per window; «resume where you were» to those that hit the wall", 15, DIM)
s += panel(400, 370, 355, 175, "#4a3320") + label(426, 408, "08:00 digest", 20, "#ffb27a", "700")
s += label(426, 444, "what waits for you:", 16, INK) + label(426, 472, "? ledger-api Deploy now?", 16, INK, "500", True) + label(426, 496, "✓ field-notes 2d  ✗ orbit-docs", 16, INK, "500", True)
s += panel(775, 370, 355, 175, "#4a3320") + label(801, 408, "02:00 night shift", 20, "#ffb27a", "700")
s += label(801, 444, '$ claude-master night add atlas-shop \\', 13, "#9ad1ff", "500", True) + label(801, 466, '    "update the changelog, run the tests"', 13, "#9ad1ff", "500", True)
s += label(801, 496, "one job at a time, while RAM and quota allow", 14, INK) + label(801, 522, "OK atlas-shop (412 s): tests green", 13, GREEN, "500", True)
s += foot("the 20:00 recap on your phone, one sentence per project; the same line lands in docs/recap.md")
cards["card8-guard"] = s + "</svg>"

# ---------------------------------------------------------------- 9 doctor
CURRENT[0] = "card9-doctor"
s = head("#1a1033", "#0c0818") + title("Doctor, for both accounts.", "Cache, hooks, poller, cron: each line says what to do")
s += panel(70, 200, 1060, 330, "#4b3a7a") + label(96, 240, "$ claude-master doctor", 22, "#9ad1ff", "700", True)
rows = [("PASS", "plugin_ok — account «personal»: claude-master 0.3.8 in the cache", GREEN), ("PASS", "plugin_ok — account «work»: claude-master 0.3.8 in the cache", GREEN),
        ("PASS", "hooks — SessionStart, Stop, PermissionRequest wired in both settings", GREEN), ("PASS", "bot — serve alive (pid 7737), last update 19:20, 0 reconnections", GREEN),
        ("WARN", "cron — registry, guard present; night 02:00 missing → night install", YEL),
        ("PASS", "shell — wrappers for both accounts, no helper needed (T76)", GREEN)]
for i, (st, t, col) in enumerate(rows):
    s += label(96, 282 + i * 34, st, 18, col, "800", True) + label(160, 282 + i * 34, t, 17, INK, "500", True)
s += label(96, 500, "0 FAIL, 1 WARN", 20, YEL, "800", True)
s += foot("run it after every update: hooks run from the cache until the session restarts")
cards["card9-doctor"] = s + "</svg>"

OUT.mkdir(parents=True, exist_ok=True)
for name, svg in cards.items():
    (OUT / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT / f"{name}.png"), output_width=1200)
print("ok", len(cards))
if WARN:
    print("SFORANO:"); print("\n".join(WARN))

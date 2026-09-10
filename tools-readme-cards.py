#!/usr/bin/env python3
"""Genera le card del README (assets/readme/*.svg + .png, 1200x630) — zero modelli, solo cairosvg."""
import cairosvg, pathlib
OUT = pathlib.Path(__file__).resolve().parent / "assets" / "readme"
FONT = "font-family=\"'DejaVu Sans',Helvetica,Arial,sans-serif\""
MONO = "'DejaVu Sans Mono',Menlo,monospace"
def head(bg1, bg2):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" {FONT} xml:space="preserve">
  <defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/></linearGradient>
  <marker id="m" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L7,3.5 L0,7 z" fill="#3ddc84"/></marker></defs>
  <rect width="1200" height="630" fill="url(#bg)"/>'''
def title(t, sub, y=92):
    return f'''<text x="70" y="{y}" fill="#ffffff" font-size="40" font-weight="800">{t}</text>
  <text x="70" y="{y+44}" fill="#b9c7e8" font-size="24">{sub}</text>'''
def label(x, y, text, size=22, color="#e8eefc", weight="500", mono=False):
    ff = f' font-family="{MONO}"' if mono else ""
    return f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}"{ff}>{text}</text>'
def tab(x, y, w, icon, color, name, active=False):
    fill = "#1e2a44" if active else "#141c30"
    o = f'<rect x="{x}" y="{y}" width="{w}" height="54" rx="10" fill="{fill}" stroke="#2f3f63"/>'
    o += f'<circle cx="{x+26}" cy="{y+27}" r="11" fill="{color}"/>' if icon == "circle" else f'<rect x="{x+15}" y="{y+16}" width="22" height="22" rx="3" fill="{color}"/>'
    return o + label(x+48, y+35, name, 19)
def panel(x,y,w,h,stroke="#2f3f63"): return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#0b1020" stroke="{stroke}"/>'
arrow = 'stroke="#3ddc84" stroke-width="3" marker-end="url(#m)"'
cards = {}
s = head("#151a33", "#0d1120") + title("One tab per session. You know which is which.", "Shape = account · colour = session · name = project folder")
tabs = [("circle","#ff8c42","claude-master",True),("circle","#7c5cff","fable-director",False),("circle","#3b9cff","site.com",False),("square","#ff4d4d","client-a",False),("square","#3ddc84","client-b",False),("square","#ffd43b","client-c",False)]
x=70
for icon,color,name,act in tabs:
    w = 200 if len(name) > 10 else 160
    s += tab(x, 195, w, icon, color, name, act); x += w+10
s += panel(70, 258, 1060, 280)
s += label(96, 300, "$ claude-master sessions", 22, "#9ad1ff", "700", True)
s += label(96, 340, "ACCOUNT        NAME             STATE     VIEW   SINCE", 19, "#7f8db0", "500", True)
rows = [("personale","claude-master","busy","open","3m"),("personale","fable-director","idle","open","2h"),("personale","site.com","waiting","-","41m"),("professionale","client-a","idle","open","5h"),("professionale","client-b","busy","open","12m")]
for i,(a,n,st,v,t) in enumerate(rows):
    col = {"busy":"#ffd43b","idle":"#3ddc84","waiting":"#ff6b6b"}[st]; y = 375 + i*32
    s += label(96, y, f"{a:<14} {n:<16} ", 19, "#e8eefc", "500", True) + label(96+11.45*32, y, f"{st:<9}", 19, col, "700", True) + label(96+11.45*42, y, f"{v:<6} {t}", 19, "#e8eefc", "500", True)
s += label(70, 590, "closing the tab closes the session: one rule, so nothing keeps running unseen in the background", 20, "#9aa7c7")
cards["card1-tabs"] = s + "</svg>"
s = head("#0f2a2a", "#08161a") + title("Talk to any session, from anywhere.", "From another session or from your phone, on either account")
s += '<rect x="80" y="220" width="150" height="270" rx="26" fill="#0b1a1a" stroke="#3ddc84" stroke-width="3"/><rect x="100" y="250" width="110" height="200" rx="8" fill="#12302e"/>'
s += label(112, 290, "is the", 18) + label(112, 316, "deploy", 18) + label(112, 342, "done?", 18) + label(96, 520, "your phone", 20, "#9aa7c7")
def box(x,y,w,h,txt,sub):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#1b3a3a" stroke="#3ddc84" stroke-width="2"/>' + label(x+20,y+42,txt,24,"#ffffff","700") + label(x+20,y+74,sub,18,"#b9e8d0")
s += box(330, 300, 300, 100, "master", "root session, your phone's door") + box(780, 200, 300, 100, "client-a", "work account") + box(780, 400, 300, 100, "site.com", "personal account")
s += f'<line x1="234" y1="350" x2="322" y2="350" {arrow}/><line x1="634" y1="330" x2="772" y2="260" {arrow}/><line x1="634" y1="370" x2="772" y2="440" {arrow}/>'
s += label(70, 555, 'claude-master talk client-a "is the deploy done?"', 20, "#9ad1ff", "700", True)
s += label(70, 583, "the answer is read from that session's own transcript", 18, "#9aa7c7") + label(70, 610, "nothing running? the Telegram bot answers /master and starts the root session for you", 18, "#9aa7c7")
cards["card2-talk"] = s + "</svg>"
s = head("#2a1a10", "#160d08") + title("After a reboot, everything comes back.", "Every session that was alive, each with its own conversation")
s += panel(70, 210, 470, 310, "#4a3320") + label(96, 252, "before the reboot", 22, "#ffb27a", "700")
for i,n in enumerate(["claude-master","fable-director","site.com","client-a","client-b","client-c"]):
    s += f'<circle cx="112" cy="{293+i*34-7}" r="8" fill="#3ddc84"/>' + label(132, 293+i*34, n, 21)
s += f'<line x1="560" y1="365" x2="640" y2="365" {arrow}/>'
s += panel(660, 210, 470, 310, "#4a3320") + label(686, 252, "the first shell asks", 22, "#ffb27a", "700")
s += label(686, 290, "machine just restarted: 6 sessions", 19, "#e8eefc", "500", True) + label(686, 316, "registered before the reboot.", 19, "#e8eefc", "500", True) + label(686, 342, "restore? [Y/n]", 19, "#e8eefc", "700", True)
for i,n in enumerate(["ok: claude-master","ok: fable-director","ok: site.com","ok: client-a","ok: client-b","ok: client-c"]):
    s += label(686, 382+i*23, n, 18, "#3ddc84", "500", True)
s += label(70, 590, "each one restarts with --continue: the conversation is where you left it", 20, "#9aa7c7")
cards["card3-reboot"] = s + "</svg>"
s = head("#1a1033", "#0c0818") + title("Evening diary, night shift.", "The day's summary on Telegram at 20:00; queued jobs run while you sleep")
s += panel(70, 200, 500, 340, "#4b3a7a") + label(96, 240, "Diary 10/09: 6 sessions, 41 turns, 3 waits", 20, "#c9b8ff", "700")
lines = [("client-a   09:12-18:40  14 turns  1 wait","#e8eefc"),("  last: deploy done, cache purged","#9aa7c7"),("site.com   10:02-alive   9 turns","#e8eefc"),("  last: three files changed, tests green","#9aa7c7"),("fable-dir  14:30-17:10   6 turns","#e8eefc"),("Totals: personale 4/28, professionale 2/13","#c9b8ff")]
for i,(a,c) in enumerate(lines): s += label(96, 282+i*32, a, 17, c, "500", True)
s += panel(630, 200, 500, 340, "#4b3a7a") + label(656, 240, "02:00: night shift", 20, "#c9b8ff", "700")
s += label(656, 282, "$ claude-master night add ~/w/client-a \\", 17, "#9ad1ff", "500", True) + label(656, 308, '    "update the changelog, run the tests"', 17, "#9ad1ff", "500", True)
for i,t in enumerate(["runs one job at a time, only while","free RAM and the account's quota allow;","a report in the project's docs/notte/,","the summary on Telegram in the morning"]):
    s += label(656, 356+i*27, t, 19)
s += label(656, 490, "OK client-a (412 s): changelog updated, tests green", 16, "#3ddc84", "500", True)
s += label(70, 590, "what happened, not what it cost: costs stay where they are measured (fable-director)", 20, "#9aa7c7")
cards["card4-night"] = s + "</svg>"
OUT.mkdir(parents=True, exist_ok=True)
for name, svg in cards.items():
    (OUT / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT / f"{name}.png"), output_width=1200)
print("ok", len(cards))

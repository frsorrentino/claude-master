#!/usr/bin/env python3
"""Verifica cm-tile.py con il chrome-bridge finto (tests/lib/fake-bridge.py) e un tmux privato.

G1  senza chrome-bridge → exit con messaggio; backend non chromeos → exit con messaggio
G2  --where elenca i monitor (dalle finestre massimizzate e dalle schede http)
G3  --dry-run: piano senza toccare niente
G4  tile: tre sessioni in tre finestre → colonne uguali nell'ordine chiesto, una per volta (T35); `actual` assente non fa morire (T37)
G5  T34: finestra massimizzata staccata in popup prima del tiling (move_tab, T36 to_window)
G6  T39: sessioni sparse su due monitor → radunate dove sta la maggioranza
G7  T40: sotto min_column_px si passa a griglia
G8  move destra: monitor dal registro; T32: voce stantia rifiutata dal tile di prova e tolta dal registro
G9  T33: pagina di riferimento scelta col CENTRO nel riquadro delle sessioni
M1  merge: sessioni in finestre diverse → segnaposto, duplicate nella finestra di raccolta (non popup, con sorgente), client NUOVO atteso (T27), schede vecchie chiuse, #home sola chiusa (T28)
M2  T28: le schede #home e quelle di attach non sono sorgenti duplicabili
L1  layout save/restore/list con l'elenco delle sessioni presenti
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
(home / ".claude" / "sessions").mkdir(parents=True)
state = tmp / "state"
bridge_state = tmp / "bridge.json"
FAKE_BRIDGE = T.ROOT / "tests" / "lib" / "fake-bridge.py"
cfg = tmp / "config.json"
URL = "chrome-untrusted://terminal/html/terminal.html"
NATIVE = {"left": 0, "top": 0, "width": 1536, "height": 864}
LEFTM = {"left": -2226, "top": -1252, "width": 2226, "height": 1204}
RIGHTM = {"left": 749, "top": -1252, "width": 2226, "height": 1204}


def write_cfg(backend="fake", cli=str(FAKE_BRIDGE)):
    cfg.write_text(json.dumps({
        "language": "it", "state_dir": str(state),
        "accounts": {"personale": {"config_dir": str(home / ".claude")}, "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
        "terminal": {"backend": backend},
        "tile": {"chrome_bridge_cli": cli, "terminal_url": "chrome-untrusted://terminal/", "min_column_px": 340,
                 "monitor_registry": str(state / "monitors.json"), "placeholder_file": str(state / "next-session"),
                 "new_client_wait_s": 15, "window_open_wait_s": 3},
        "tabs": {"color_registry": str(tmp / "colors")},
    }))


write_cfg()


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "FAKE_BRIDGE_STATE": str(bridge_state), "CM_PROC_SCAN_PIDS": "",
         "FAKE_BRIDGE_PLACEHOLDER": str(state / "next-session"), "FAKE_BRIDGE_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}
    e.update(extra)
    return e


def tile(*args, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tile.py")] + list(args), capture_output=True, text=True, env=env(**extra), timeout=180)


def term_tab(tid, wid, name):
    return {"id": tid, "windowId": wid, "url": f"{URL}?command=claude-master&args[]=attach&args[]={name}&args[]=ephemeral", "title": f"🔴 {name}", "active": True}


def win(wid, l, t, w=600, h=500, state_="normal", type_="popup"):
    return {"id": wid, "type": type_, "state": state_, "left": l, "top": t, "width": w, "height": h}


def world(windows, tabs, monitors):
    bridge_state.write_text(json.dumps({"windows": windows, "tabs": tabs, "monitors": monitors, "calls": []}))


def calls():
    return json.load(open(bridge_state))["calls"]


def windows():
    return {x["id"]: x for x in json.load(open(bridge_state))["windows"]}


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "bash", "--norc"], env=env(), check=True)
    for s in ("beta", "gamma"):
        tm("new-session", "-d", "-s", s, "bash", "--norc")
    state.mkdir()
    # G1
    write_cfg(cli="")
    r = tile("tile")
    T.check("G1 no chrome-bridge → refuses with message", r.returncode != 0 and "chrome-bridge" in (r.stderr + r.stdout), r.stderr)
    write_cfg(backend="gnome")
    r = tile("tile")
    T.check("G1 backend not chromeos → refuses", r.returncode != 0 and "chromeos" in (r.stderr + r.stdout), r.stderr)
    write_cfg()
    # G2: monitor da una massimizzata (nativo) e da una scheda http su sinistra
    world([win(1, 0, 0, 1536, 864, "maximized", "app"), win(2, -2000, -1000, 800, 600, "normal", "normal")],
          [{"id": 1, "windowId": 1, "url": URL + "#home", "title": "Terminal", "active": True},
           {"id": 2, "windowId": 2, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE, LEFTM])
    r = tile("tile", "--where")
    T.check("G2 --where lists native and above/external monitors", r.returncode == 0 and "nativo" in r.stdout and "1536x864" in r.stdout and "2226x1204" in r.stdout, r.stdout + r.stderr)
    # G3/G4: tre sessioni, tre finestre popup sul nativo + una finestra http di riferimento
    world([win(11, 10, 10), win(12, 620, 10), win(13, 10, 520), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), term_tab(103, 13, "gamma"),
           {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE, LEFTM])
    r = tile("tile", "gamma", "alfa", "beta", "--dry-run")
    T.check("G3 dry-run plans 3 columns and touches nothing", r.returncode == 0 and "3 " in r.stdout and windows()[11]["left"] == 10 and not any(c["cmd"] == "tile_windows" for c in calls()), r.stdout + r.stderr)
    r = tile("tile", "gamma", "alfa", "beta")
    ws = windows()
    T.check("G4 columns in the requested order: gamma, alfa, beta", r.returncode == 0 and ws[13]["left"] == 0 and ws[11]["left"] == 512 and ws[12]["left"] == 1024 and ws[13]["width"] == 512, r.stdout + r.stderr + str({k: (v['left'], v['width']) for k, v in ws.items()}))
    tiles = [c for c in calls() if c["cmd"] == "tile_windows"]
    T.check("G4 one tile_windows call per window (T35)", len(tiles) == 3 and all(len(c["params"]["window_ids"]) == 1 for c in tiles), str([c["params"]["window_ids"] for c in tiles]))
    r = tile("tile", "gamma", "alfa", "beta")
    T.check("G4 re-run with windows already in place: no crash (T37), reports ok", r.returncode == 0 and "3/3" in r.stdout, r.stdout + r.stderr)
    # G5: massimizzata
    world([win(11, 0, 0, 1536, 864, "maximized", "app"), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", "alfa", "beta")
    T.check("G5 maximized window detached into a popup, then tiled", r.returncode == 0 and any(c["cmd"] == "move_tab" and c["params"]["tab_id"] == 101 for c in calls()) and "2/2" in r.stdout, r.stdout + r.stderr)
    # G5b: minimizzata (T72): stessa sorte della massimizzata
    world([win(11, 795, 0, 573, 864, "minimized", "app"), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", "alfa", "beta")
    T.check("G5b minimized window detached into a popup, then tiled", r.returncode == 0 and any(c["cmd"] == "move_tab" and c["params"]["tab_id"] == 101 for c in calls()) and "2/2" in r.stdout and "minimized" in r.stdout, r.stdout + r.stderr)
    # G5c: sei sessioni su 1536 px = 256 per colonna < 340 → griglia 3x2 da 512
    for s_ in ("delta", "epsilon", "zeta"):
        tm("new-session", "-d", "-s", s_, "bash", "--norc")
    six = ["alfa", "beta", "gamma", "delta", "epsilon", "zeta"]
    world([win(10 + i, 10 + 40 * i, 10) for i in range(6)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(100 + i, 10 + i, n_) for i, n_ in enumerate(six)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *six)
    ws = windows()
    T.check("G5c 6 columns below min_column_px → automatic 3x2 grid", r.returncode == 0 and "griglia" in r.stdout or "grid" in r.stdout, r.stdout + r.stderr)
    T.check("G5c grid cells are 512 wide, second row at top 432", ws[10]["width"] == 512 and ws[13]["left"] == 0 and ws[13]["top"] == 432, str({k: (v["left"], v["top"], v["width"]) for k, v in ws.items()}))
    for s_ in ("delta", "epsilon", "zeta"):
        tm("kill-session", "-t", f"={s_}")
    # G6: sparse su due monitor → maggioranza (sinistra)
    world([win(11, -2000, -1000), win(12, -1300, -1000), win(13, 100, 100), win(9, -2200, -600, 700, 300, "normal", "normal"), win(8, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), term_tab(103, 13, "gamma"),
           {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}, {"id": 80, "windowId": 8, "url": "https://example.org/", "title": "ex2", "active": True}], [NATIVE, LEFTM])
    r = tile("tile", "alfa", "beta", "gamma")
    ws = windows()
    T.check("G6 scattered sessions gathered on the majority monitor (the external one)", r.returncode == 0 and ws[13]["left"] < 0 and "2 monitor" in r.stdout, r.stdout + r.stderr + str({k: v['left'] for k, v in ws.items()}))
    # G7: griglia sotto 340 px per colonna: 6 sessioni sul nativo (1536/6 = 256)
    for s in ("d1", "d2", "d3"):
        tm("new-session", "-d", "-s", s, "bash", "--norc")
    world([win(11, 10, 10), win(12, 620, 10), win(13, 10, 520), win(14, 300, 300), win(15, 400, 300), win(16, 500, 300), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), term_tab(103, 13, "gamma"), term_tab(104, 14, "d1"), term_tab(105, 15, "d2"), term_tab(106, 16, "d3"),
           {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", "alfa", "beta", "gamma", "d1", "d2", "d3", "--grid")
    ws = windows()
    T.check("G7 grid 3x2 with 512px cells", r.returncode == 0 and ws[11]["width"] == 512 and ws[14]["top"] == 432 and "grid" in r.stdout, r.stdout + r.stderr + str({k: (v['left'], v['top'], v['width']) for k, v in ws.items()}))
    for s in ("d1", "d2", "d3"):
        tm("kill-session", "-t", f"={s}")
    # G8: move destra dal registro dei monitor (nessuna finestra la' sopra); poi voce stantia
    (state / "monitors.json").write_text(json.dumps([RIGHTM]))
    world([win(11, 10, 10), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE, RIGHTM])
    r = tile("move", "destra", "alfa", "beta")
    ws = windows()
    T.check("G8 move destra tiles on the right monitor known from the registry", r.returncode == 0 and ws[11]["left"] == 749 and ws[12]["left"] == 749 + 1113, r.stdout + r.stderr + str({k: v['left'] for k, v in ws.items()}))
    (state / "monitors.json").write_text(json.dumps([{"left": 5000, "top": -1252, "width": 2226, "height": 1204}]))
    world([win(11, 10, 10), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("move", "sopra", "alfa", "beta")
    T.check("G8 stale registry entry rejected by the probe tile and removed (T32)", r.returncode != 0 and "stantia" in (r.stdout + r.stderr) and not any(m["left"] == 5000 for m in json.loads((state / "monitors.json").read_text())), r.stdout + r.stderr + (state / "monitors.json").read_text())
    # G9: pagina di riferimento col centro nel riquadro: la finestra 8 sfiora il bordo ma il centro sta fuori
    world([win(11, 10, 10), win(12, 620, 10), win(8, 1200, 300, 700, 300, "normal", "normal"), win(9, 300, 300, 200, 100, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 80, "windowId": 8, "url": "https://far.example/", "title": "far", "active": True},
           {"id": 90, "windowId": 9, "url": "https://near.example/", "title": "near", "active": True}], [NATIVE])
    r = tile("tile", "alfa", "beta")
    js = [c for c in calls() if c["cmd"] == "execute_js"]
    T.check("G9 reference page chosen by centre inside the sessions' box (near, not far)", r.returncode == 0 and js and js[-1]["params"]["tab_id"] == 90, str([c["params"]["tab_id"] for c in js]))
    # M1: merge — finestra di raccolta 5 (normal) con una shell duplicabile; alfa in popup 11
    world([win(5, 0, 0, 1536, 864, "normal", "app"), win(11, 10, 10), win(7, 900, 100, 400, 300, "normal", "app")],
          [{"id": 50, "windowId": 5, "url": URL, "title": "Terminal", "active": True}, term_tab(101, 11, "alfa"),
           {"id": 70, "windowId": 7, "url": URL + "#home", "title": "Terminal", "active": True}], [NATIVE])
    r = tile("merge", "alfa")
    ws = windows()
    tabs = json.load(open(bridge_state))["tabs"]
    T.check("M1 merge: placeholder consumed, tab duplicated in the collecting window, old popup closed, lone #home closed",
            r.returncode == 0 and any(c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate" for c in calls())
            and 11 not in ws and 7 not in ws and any(t["windowId"] == 5 and "args[]=alfa" in t["url"] for t in tabs) and not (state / "next-session").exists(),
            r.stdout + r.stderr + str(list(ws)) + str(tabs))
    T.check("M1 a NEW client attached to alfa (T27)", "alfa 1" in tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout, tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout)
    # M2: senza sorgente duplicabile (solo #home e schede di attach) → non raccoglie li'
    world([win(5, 0, 0, 1536, 864, "normal", "app"), win(11, 10, 10)],
          [{"id": 50, "windowId": 5, "url": URL + "#home", "title": "Terminal", "active": True}, term_tab(51, 5, "beta"), term_tab(101, 11, "gamma")], [NATIVE])
    r = tile("merge", "gamma", CM_TILE_NO_GARCON="1")
    T.check("M2 no duplicable source (#home, attach tabs) → no duplicate from them", not any(c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate" and c["params"].get("tab_id") in (50, 51) for c in calls()), str(calls()[-3:]))
    # L1
    world([win(11, 10, 10), win(12, 620, 10)], [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta")], [NATIVE])
    r = tile("layout", "save", "mattina")
    T.check("L1 layout save records the live sessions", r.returncode == 0 and (state / "layouts" / "mattina.json").exists() and "alfa" in (state / "layouts" / "mattina.json").read_text(), r.stdout + r.stderr)
    tm("kill-session", "-t", "=beta")
    r = tile("layout", "restore", "mattina")
    T.check("L1 layout restore names the missing session", r.returncode == 0 and "beta" in r.stdout and "restored" in r.stdout, r.stdout + r.stderr)
    r = tile("layout", "list")
    T.check("L1 layout list", "mattina" in r.stdout, r.stdout + r.stderr)

T.rm(str(tmp))
T.finish()

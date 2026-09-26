#!/usr/bin/env python3
"""claude-master model / effort — cambiare modello o effort di un'altra sessione, SOLO per quella sessione.

  claude-master model  <nome> <modello>   uno degli id di tune.models (per esempio claude-sonnet-5)
  claude-master effort <nome> <livello>   uno di tune.efforts (low, medium, high, xhigh, max)

Scritti come comando («/model sonnet», «/effort medium») i due cambi salvano anche il DEFAULT per le sessioni
nuove: Claude Code riscrive ~/.claude/settings.json sostituendo il file (provato il 16/09/2026 alle 11:30, l'inode
cambia), quindi nessun lock e nessun ripristino avrebbero protetto le altre sessioni. Aperti SENZA argomento, i due
selettori offrono invece «s to use this session only»: il cambio vale per quella sessione e settings.json resta
identico (provato nello stesso giro: «Set model to Sonnet 5 for this session only», «Set effort level to low (this
session only)»). Qui si guida il selettore e si preme `s`, mai Invio.

Il modello si cerca per ETICHETTA nel selettore (tune.models[].pick), non per posizione: l'ordine delle voci puo'
cambiare fra versioni e account. L'effort e' un cursore orizzontale: si porta in fondo a sinistra e poi avanti di
quanti gradini serve. Nel selettore del modello le frecce laterali cambiano l'effort, quindi li' solo Su e Giu'.

Si agisce solo su una sessione ferma al prompt: niente spinner di lavoro, nessun dialogo aperto, niente testo
scritto dall'utente sulla riga del prompt (il suggerimento grigio di Claude Code non conta: SGR 2). Il risultato vale
solo se nel riquadro compare la conferma «this session only»; altrimenti Esc e un errore.

Uscite: 0 fatto; 1 sessione assente; 2 valore o uso non validi; 3 sessione non ferma al prompt; 4 selettore o
conferma non arrivati. La prima riga dell'output e' il testo per chi chiama (il relay la gira al polso).
"""
import importlib.util
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
T = CFG.get("tune") or {}
WAIT_S = float(T.get("timeout_s") or 8)
ORDER = ["low", "medium", "high", "xhigh", "max", "ultracode"]   # le posizioni del cursore, da sinistra
# Claude Code 2.1.283 (dal vivo il 26/09/2026): la lista di /model scorre — sette voci a schermo su undici, «↑ n.» sulla
# prima riga visibile e «↓ n.» sull'ultima quando ce ne sono altre, «… +4 models» in coda; sulla riga del cursore la
# freccia lascia il posto a «❯». Giu' oltre l'ultima voce torna alla prima.
PICK_ROW = re.compile(r"^\s*[↑↓]?\s*(❯)?\s*[↑↓]?\s*(\d+)\.\s+(.+?)(?:\s+✔)?(?:\s{2,}.*)?$")
DIM = re.compile(r"\x1b\[[0-9;]*m")


def tmux(*args):
    import subprocess
    return subprocess.run(sessions.TMUX + list(args), capture_output=True, text=True)


def screen(name, escapes=False):
    return tmux("capture-pane", "-p", "-J", *(["-e"] if escapes else []), "-t", name).stdout


def keys(name, *k, pause=0.12):
    for x in k:
        tmux("send-keys", "-t", name, x)
        time.sleep(pause)


def flat(scr):
    """Il testo del riquadro su una riga sola, spazi compressi. In un riquadro stretto (una sessione lanciata senza
    finestra ne ha circa 34 colonne) Claude Code va a capo da se' dentro le frasi: «s to use this session only»
    arrivava spezzato su due righe e la conferma non si trovava (provato dal vivo il 16/09 alle 11:50). `-J` unisce
    solo gli a capo di tmux, non questi: le frasi si cercano qui."""
    return " ".join(str(scr or "").split())


def below_prompt(scr):
    """Il testo appiattito SOTTO l'ultima riga che comincia con «❯». Un dialogo aperto sta li' (le sue voci col
    cursore e poi il piè «… session only»); un selettore gia' chiuso sta sopra il prompt nuovo. Dopo un
    ridimensionamento tmux riallinea le righe vecchie e il piè di un selettore chiuso ricompariva sopra il prompt:
    cercarlo in tutto lo schermo faceva credere aperto un dialogo che non c'era (visto nei test il 16/09)."""
    lines = str(scr or "").splitlines()
    last = max((i for i, l in enumerate(lines) if l.lstrip().startswith("❯")), default=-1)
    return flat("\n".join(lines[last + 1:] if last >= 0 else lines)) if last >= 0 else flat(scr)


def wait_new(name, pattern, before, timeout=None):
    """L'ultima corrispondenza di `pattern` quando ce n'e' una in piu' di `before`: una conferma rimasta a schermo da
    un cambio precedente non deve passare per quella di questo cambio."""
    end = time.time() + (timeout or WAIT_S)
    while time.time() < end:
        found = re.findall(pattern, flat(screen(name)))
        if len(found) > before:
            return found[-1]
        time.sleep(0.25)
    return None


def wait_for(name, pred, timeout=None):
    """Il riquadro appena `pred` e' vero sul testo appiattito; "" allo scadere."""
    end = time.time() + (timeout or WAIT_S)
    while time.time() < end:
        scr = screen(name)
        if pred(flat(scr)):
            return scr
        time.sleep(0.25)
    return ""


def models():
    return [m for m in (T.get("models") or []) if m.get("id") and m.get("pick")]


def picks(m):
    """Le etichette del selettore che valgono per un modello: `pick` stringa o lista. Le etichette cambiano con la
    versione di Claude Code («Opus (1M context)», «Fable», «Sonnet» fino alla 2.1.280; «Opus 5.5», «Fable 5.1»,
    «Sonnet 5» dalla 2.1.283): una lista regge entrambe."""
    p = m.get("pick")
    return [p] if isinstance(p, str) else [x for x in (p or []) if isinstance(x, str)]


def efforts():
    return [e for e in (T.get("efforts") or []) if e in ORDER]


def prompt_ready(name):
    """(True, "") se la sessione e' ferma al prompt vuoto; altrimenti (False, motivo)."""
    plain = flat(screen(name))
    if re.search(r"(?i)esc to interrupt", plain):
        return False, "busy"
    tail = below_prompt(screen(name))
    if "Enter to select" in tail or "to use this session only" in tail or "to adjust" in tail:
        return False, "dialog"
    colored = screen(name, escapes=True).splitlines()
    rows = [l for l in colored if DIM.sub("", l).lstrip().startswith("❯")]
    if not rows:
        return False, "no_prompt"
    last = rows[-1]
    after = last.split("❯", 1)[1] if "❯" in last else ""
    # il testo grigio (SGR 2) dopo ❯ e' un suggerimento di Claude Code, non testo dell'utente
    typed = re.sub(r"\x1b\[2m.*?(\x1b\[(?:0|22)?m|$)", "", after)
    if DIM.sub("", typed).strip():
        return False, "typed"
    return True, ""


def remember(name, **fields):
    """Annota il cambio riuscito per session_id in tune.file: la trascrizione lo riporta solo al turno dopo, e lo
    stato del polso deve dire subito il valore nuovo (chiesto dall'app il 16/09 alle 11:33). cm-core lo usa finche'
    la sessione non scrive un turno piu' recente dell'annotazione; da li' vince di nuovo la trascrizione."""
    import json, os, tempfile
    try:
        row = next((r for r in sessions.collect(read_screen=False) if (r.get("tmux") or r.get("name")) == name), None)
    except Exception:   # noqa: BLE001 — l'annotazione e' un aiuto per lo stato, il cambio e' gia' avvenuto
        row = None
    sid = (row or {}).get("session_id")
    if not sid:
        return
    p = Path(cm.expand(T.get("file") or "")) if T.get("file") else None
    if not p:
        return
    try:
        data = json.loads(p.read_text()) if p.is_file() else {}
    except (OSError, ValueError):
        data = {}
    entry = data.get(sid) or {}
    entry.update(fields)
    entry["at"] = time.time()
    data[sid] = entry
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def picker_rows(scr):
    """[(n, etichetta, cursore)] delle SOLE voci del selettore: le righe dopo l'ultimo «Select model». Sopra c'e' la
    conversazione, e un elenco numerato scritto da Claude («4. Sonnet …») non deve diventare una voce."""
    lines = scr.splitlines()
    start = max((i for i, l in enumerate(lines) if "Select model" in l), default=-1)
    if start < 0:
        return []
    return [(int(m.group(2)), m.group(3).strip(), bool(m.group(1))) for m in map(PICK_ROW.match, lines[start + 1:]) if m]


class wide:
    """Allarga il riquadro per il tempo del cambio, se nessuno lo sta guardando, e poi lo rimette com'era.

    Una sessione lanciata senza finestra ha un riquadro di 34 colonne: li' il selettore di /model diventa una lista
    che scorre, le etichette si troncano («Opus (1M c… ✔») e la riga «s to use this session only» resta sotto il
    bordo — dal vivo il 16/09 alle 11:55, e nessuna lettura del testo puo' trovare cio' che non e' a schermo. Con un
    client collegato (una scheda del terminale aperta) il riquadro non si tocca mai: e' la finestra di qualcuno."""

    MIN_COLS = 100

    def __init__(self, name):
        self.name, self.old = name, None

    def __enter__(self):
        r = tmux("display-message", "-p", "-t", self.name, "#{session_attached} #{window_width} #{window_height}")
        try:
            attached, w, h = (int(x) for x in r.stdout.split())
        except ValueError:
            return self
        if attached == 0 and w < self.MIN_COLS:
            self.old = (w, h)
            tmux("resize-window", "-t", self.name, "-x", "160", "-y", str(max(h, 50)))
            time.sleep(0.8)   # Claude Code ridisegna alla nuova misura
        return self

    def __exit__(self, *exc):
        if self.old:
            tmux("resize-window", "-t", self.name, "-x", str(self.old[0]), "-y", str(self.old[1]))
        return False


def open_picker(name, command, marker):
    tmux("send-keys", "-t", name, "-l", command)
    time.sleep(0.2)
    tmux("send-keys", "-t", name, "Enter")
    end = time.time() + WAIT_S
    while time.time() < end:
        scr = screen(name)
        if marker in below_prompt(scr):
            return scr
        time.sleep(0.25)
    return ""


def cancel(name):
    keys(name, "Escape", pause=0.3)


def _set_model(name, wanted):
    choice = next((m for m in models() if wanted in (m["id"], m.get("label")) or wanted in picks(m)), None)
    if not choice:
        print(M("tune.bad_model", model=wanted, choices=", ".join(m["id"] for m in models())))
        return 2
    ok, why = prompt_ready(name)
    if not ok:
        print(M(f"tune.not_ready_{why}", name=name))
        return 3
    scr = open_picker(name, "/model", "to use this session only")
    if not scr:
        cancel(name)
        print(M("tune.no_picker", name=name, what="/model"))
        return 4
    # verso la voce: di un salto quando e' a schermo, altrimenti una riga alla volta (la lista scorre e in fondo torna
    # in cima: un numero di riga gia' visto sotto il cursore vuol dire che la voce non c'e' in tutta la lista)
    seen, on = set(), None
    for _ in range(40):
        rows = picker_rows(screen(name))
        cur = next(((n, label) for n, label, c in rows if c), None)
        if not cur or cur[0] in seen:
            break
        if cur[1] in picks(choice):
            on = cur[1]
            break
        seen.add(cur[0])
        target = next((n for n, label, _ in rows if label in picks(choice)), 0)
        keys(name, *((["Down" if target > cur[0] else "Up"] * abs(target - cur[0])) if target else ["Down"]))
        time.sleep(0.3)
    if not on:
        cancel(name)
        print(M("tune.no_option", name=name, label=picks(choice)[0]))
        return 4
    pat = r"Set model to (.+?) for this session only"
    before = len(re.findall(pat, flat(screen(name))))
    keys(name, "s", pause=0.3)
    line = wait_new(name, pat, before)
    if not line:
        cancel(name)
        print(M("tune.no_confirm", name=name))
        return 4
    line = line.strip()
    remember(name, model={"id": choice["id"], "label": choice.get("label") or line})
    print(M("tune.model_set", name=name, model=line))
    return 0


def _set_effort(name, level):
    if level not in efforts():
        print(M("tune.bad_effort", effort=level, choices=", ".join(efforts())))
        return 2
    ok, why = prompt_ready(name)
    if not ok:
        print(M(f"tune.not_ready_{why}", name=name))
        return 3
    if not open_picker(name, "/effort", "for this session only"):
        cancel(name)
        print(M("tune.no_picker", name=name, what="/effort"))
        return 4
    # il cursore parte dal livello attuale: prima tutto a sinistra (low), poi avanti fino al livello voluto
    keys(name, *(["Left"] * len(ORDER)), *(["Right"] * ORDER.index(level)))
    pat = r"Set effort level to (\w+) \(this session only\)"
    before = len(re.findall(pat, flat(screen(name))))
    keys(name, "s", pause=0.3)
    got = wait_new(name, pat, before)
    if not got:
        cancel(name)
        print(M("tune.no_confirm", name=name))
        return 4
    if got != level:
        print(M("tune.effort_other", name=name, got=got, effort=level))
        return 4
    remember(name, effort=level)
    print(M("tune.effort_set", name=name, effort=level))
    return 0


def set_model(name, wanted):
    with wide(name):
        return _set_model(name, wanted)


def set_effort(name, level):
    with wide(name):
        return _set_effort(name, level)


def main(argv):
    if len(argv) < 3 or argv[0] not in ("model", "effort"):
        print(M("tune.usage"), file=sys.stderr)
        return 2
    what, name, value = argv[0], argv[1], argv[2]
    if tmux("has-session", "-t", f"={name}").returncode != 0:
        print(M("tune.no_session", name=name))
        return 1
    return set_model(name, value) if what == "model" else set_effort(name, value)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

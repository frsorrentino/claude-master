#!/usr/bin/env python3
"""Verifica cm-tune.py (claude-master model / effort, contratto 1.12) con il claude finto e un tmux privato.

TU1  model: dal selettore di /model, «s» → «Set model to Sonnet 5 for this session only», uscita 0
TU2  model per id, spostandosi verso l'alto (Sonnet → Fable); Opus 5.5 per id sulla voce «Opus (1M context)» (2.1.280)
TU3  effort: cursore tutto a sinistra e poi avanti, «s» → «(this session only)», per low e per max
TU4  valori fuori dalle scelte (modello sconosciuto, ultracode) → uscita 2, nessun tasto mandato
TU5  sessione al lavoro («esc to interrupt») → uscita 3, niente selettore aperto
TU6  domanda aperta («Enter to select») → uscita 3
TU7  testo scritto dall'utente nel prompt → uscita 3, il testo resta com'era
TU8  selettore che non si apre → uscita 4 dopo tune.timeout_s
TU9  mai Invio in un selettore: il default per le sessioni nuove non viene salvato (FAKE_CLAUDE_DEFAULT_LOG vuoto)
TU10 sessione inesistente → uscita 1
TU11 riquadro stretto (34 colonne, come una sessione lanciata senza finestra): le frasi vanno a capo DENTRO, e il
     cambio riesce lo stesso (dal vivo il 16/09 alle 11:50 non trovava «s to use this session only»)
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
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"),
                           "tune": {"timeout_s": 3, "file": str(tmp / "tuned.json")}}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
DEFAULT_LOG = tmp / "default.log"


def env(tm):
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
            "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}


def tune(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tune.py"), *args], capture_output=True, text=True, env=env(tm), timeout=60)


def start(tm, name, scenario="plain", cols=160):
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", name, "-x", str(cols), "-y", "40",
                    "env", f"FAKE_CLAUDE_SCENARIO={scenario}", "FAKE_CLAUDE_REGISTER=0", f"FAKE_CLAUDE_DEFAULT_LOG={DEFAULT_LOG}", str(FAKE)],
                   env=env(tm), check=True)
    time.sleep(2)


def pane(tm, name):
    return subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-J", "-t", name], capture_output=True, text=True).stdout


with T.PrivateTmux() as tm:
    start(tm, "alfa")
    r = tune(tm, "model", "alfa", "claude-sonnet-5")
    T.check("TU1 model from the /model picker with «s»: exit 0, «Sonnet 5, solo questa sessione»",
            r.returncode == 0 and "Sonnet 5" in r.stdout and "solo questa sessione" in r.stdout, r.stdout + r.stderr)
    T.check("TU1 the pane shows the session-only confirmation", "Set model to Sonnet 5 for this session only" in pane(tm, "alfa"), pane(tm, "alfa"))
    r = tune(tm, "model", "alfa", "claude-fable-5-1")
    T.check("TU2 model by id, moving up from the current entry: Fable 5.1", r.returncode == 0 and "Fable 5.1" in r.stdout, r.stdout + r.stderr)
    r = tune(tm, "model", "alfa", "claude-opus-5-5[1m]")
    T.check("TU2 Opus 5.5 by id: the «Opus (1M context)» entry of the 2.1.280 picker", r.returncode == 0 and "Opus 5.5" in r.stdout
            and "Set model to Opus 5.5 for this session only" in pane(tm, "alfa"), r.stdout + r.stderr + pane(tm, "alfa"))
    r = tune(tm, "model", "alfa", "claude-opus-5[1m]")
    T.check("TU4 Opus 5 is no longer a choice (no picker entry since 2.1.280) → exit 2", r.returncode == 2, r.stdout + r.stderr)
    r = tune(tm, "effort", "alfa", "low")
    T.check("TU3 effort low: cursor to the left end, «s» → «(this session only)»",
            r.returncode == 0 and "effort low" in r.stdout and "Set effort level to low (this session only)" in pane(tm, "alfa"), r.stdout + r.stderr + pane(tm, "alfa"))
    r = tune(tm, "effort", "alfa", "max")
    T.check("TU3 effort max: four steps right from low, never onto ultracode", r.returncode == 0 and "effort max" in r.stdout, r.stdout + r.stderr + pane(tm, "alfa"))
    before = pane(tm, "alfa")
    r1 = tune(tm, "model", "alfa", "gpt-9")
    r2 = tune(tm, "effort", "alfa", "ultracode")
    T.check("TU4 a model or an effort outside the choices → exit 2, and no key reaches the session",
            r1.returncode == 2 and "non disponibile" in r1.stdout and r2.returncode == 2 and "non disponibile" in r2.stdout and pane(tm, "alfa") == before,
            r1.stdout + r2.stdout)

    start(tm, "beta", "busy")
    r = tune(tm, "effort", "beta", "low")
    T.check("TU5 a session at work («esc to interrupt») → exit 3, no picker opened", r.returncode == 3 and "sta lavorando" in r.stdout and "to adjust" not in pane(tm, "beta"), r.stdout + pane(tm, "beta"))

    start(tm, "gamma", "question")
    r = tune(tm, "model", "gamma", "claude-sonnet-5")
    T.check("TU6 a question open («Enter to select») → exit 3", r.returncode == 3 and "domanda aperta" in r.stdout, r.stdout + pane(tm, "gamma"))

    start(tm, "delta")
    subprocess.run(["tmux", "-L", tm.socket, "send-keys", "-t", "delta", "-l", "ciao"], check=True)
    time.sleep(0.5)
    r = tune(tm, "effort", "delta", "low")
    T.check("TU7 text typed by the user in the prompt → exit 3, the text is left as it was",
            r.returncode == 3 and "testo scritto" in r.stdout and "ciao" in pane(tm, "delta") and "to adjust" not in pane(tm, "delta"), r.stdout + pane(tm, "delta"))

    start(tm, "eta", "nopicker")
    t0 = time.time()
    r = tune(tm, "model", "eta", "claude-sonnet-5")
    T.check("TU8 a picker that never opens → exit 4 after tune.timeout_s", r.returncode == 4 and "non si è aperto" in r.stdout and time.time() - t0 < 15, r.stdout + pane(tm, "eta"))

    T.check("TU9 Enter was never pressed in a picker: no default for new sessions was saved",
            not DEFAULT_LOG.exists() or not DEFAULT_LOG.read_text().strip(), DEFAULT_LOG.read_text() if DEFAULT_LOG.exists() else "")
    start(tm, "stretta", cols=34)
    r1 = tune(tm, "model", "stretta", "claude-haiku-4-5")
    r2 = tune(tm, "effort", "stretta", "xhigh")
    size = subprocess.run(["tmux", "-L", tm.socket, "display", "-p", "-t", "stretta", "#{window_width}"], capture_output=True, text=True).stdout.strip()
    T.check("TU11 a 34-column pane, where the picker's last line is below the edge: widened for the change, model and effort succeed",
            r1.returncode == 0 and "Haiku 4.5" in r1.stdout and r2.returncode == 0 and "effort xhigh" in r2.stdout, r1.stdout + r2.stdout + pane(tm, "stretta"))
    T.check("TU11 the pane is back to its own 34 columns afterwards", size == "34", size)
    start(tm, "media", cols=50)
    subprocess.run(["tmux", "-L", tm.socket, "resize-window", "-t", "media", "-x", "50", "-y", "40"], check=True)
    r = tune(tm, "model", "media", "claude-sonnet-5")
    T.check("TU11 a 50-column pane, sentences wrapped inside the line: still found", r.returncode == 0 and "Sonnet 5" in r.stdout, r.stdout + pane(tm, "media"))
    r = tune(tm, "model", "zeta", "claude-sonnet-5")
    T.check("TU10 no such session → exit 1", r.returncode == 1 and "zeta" in r.stdout, r.stdout + r.stderr)
T.finish()

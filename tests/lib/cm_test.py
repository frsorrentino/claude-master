"""Harness dei test di claude-master.

Nessun framework: ogni test e' uno script che stampa OK/FAIL per caso ed esce
con codice diverso da zero se uno fallisce (convenzione di fable-director).

Fornisce:
- check(name, ok, detail): registra e stampa;
- finish(): riepilogo + exit;
- fake_home(): una HOME finta con la forma di questa macchina (due account,
  workspace, script legacy) per i test di init/doctor, senza toccare la vera;
- fake_machine(): il file JSON che cm-config.py legge al posto di /proc, PATH e
  variabili d'ambiente quando CM_FAKE_MACHINE e' impostata;
- run_config(): invoca cm-config.py con HOME e config isolate;
- PrivateTmux: un server tmux su socket privato (-L), ucciso a fine test.
  Nessun test tocca il server tmux vero: le sessioni di lavoro non si toccano.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN = ROOT / "claude-master"
SCRIPTS = PLUGIN / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"

FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def finish():
    print(f"\n{COUNT - len(FAILS)}/{COUNT} OK" + (f", FAIL: {', '.join(FAILS)}" if FAILS else ""))
    sys.exit(1 if FAILS else 0)


def fake_home(tmp, legacy=True, second_account=True, workspace=True, language="Italiano"):
    """HOME finta con la forma di questa macchina (08/09/2026).

    legacy=True: skill nuova-sessione, registro sessioni, registro colori,
    wrapper claude-pixel nel .bashrc, hook ora locale nei settings — cioe' lo
    stato «prima del plugin» che init deve riconoscere.
    """
    home = Path(tmp) / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    (home / ".claude" / "settings.json").write_text(json.dumps({
        **({"language": language} if language else {}),
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                  "command": "date '+%A %d/%m/%Y %H:%M' | sed 's/^/[ora locale] /'"}]}]},
        "mcpServers": {"chrome-bridge": {"command": "node",
                       "args": [str(home / "cb" / "server" / "index.js")]}},
    }))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"chrome-bridge": {
        "type": "stdio", "command": "node", "args": [str(home / "cb" / "server" / "index.js")]}}}))
    (home / "cb" / "server").mkdir(parents=True)
    (home / "cb" / "server" / "index.js").write_text("")
    (home / "cb" / "server" / "cli.js").write_text("")
    if second_account:
        (home / ".claude-pixel").mkdir()
        (home / ".claude-pixel" / ".credentials.json").write_text("{}")
        (home / ".claude-pixel" / "settings.json").write_text(json.dumps({
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                      "command": "date '+%A %d/%m/%Y %H:%M' | sed 's/^/[ora locale] /'"}]}]}}))
    if workspace:
        ws = home / "Desktop" / "workspaces"
        for d in ("personali/alfa", "personali/beta", "pixelfarm/clienti/sito.com",
                  "pixelfarm/nostri/hub", "_archivio", "_prod_backups", ".claude"):
            (ws / d).mkdir(parents=True)
    if legacy:
        sk = home / ".claude" / "skills" / "nuova-sessione"
        sk.mkdir(parents=True)
        (sk / "lancia.sh").write_text("#!/bin/sh\n")
        (home / ".claude" / "sessioni-vive.json").write_text(json.dumps({
            "salvato": "2026-09-08T22:00:00+02:00",
            "sessioni": [
                {"nome": "master", "cartella": str(home / "Desktop/workspaces"), "account": "personale"},
                {"nome": "alfa", "cartella": str(home / "Desktop/workspaces/personali/alfa"), "account": "personale"},
                {"nome": "pix-sito-com", "cartella": str(home / "Desktop/workspaces/pixelfarm/clienti/sito.com"), "account": "aziendale"},
                {"nome": "pix-hub", "cartella": str(home / "Desktop/workspaces/pixelfarm/nostri/hub"), "account": "aziendale"},
            ]}))
        (home / ".claude" / "colori-sessione").write_text("master\t2\nalfa\t0\npix-sito-com\t3\n")
        (home / ".bashrc").write_text(
            'claude() { command claude "$@"; }\n'
            '# >>> claude-pixel >>>\n'
            'claude-pixel() {\n'
            '  CLAUDE_CONFIG_DIR="$HOME/.claude-pixel" command claude "$@"\n'
            '}\n')
        (home / ".tmux.conf").write_text(
            'bind a run-shell -b "$HOME/.local/bin/affianca"\n'
            'bind u run-shell -b "$HOME/.local/bin/unisci"\n'
            'bind -r Left run-shell -b "$HOME/.local/bin/sposta sinistra"\n')
    return home


def fake_machine(tmp, home, chromeos=True, procs=None, which=None, env=None):
    """Descrizione della macchina finta letta da cm-config.py (CM_FAKE_MACHINE)."""
    m = {
        "which": which if which is not None else ["tmux", "claude", "python3", "gnome-terminal"],
        "exists": [ "/opt/google/cros-containers/bin/garcon" ] if chromeos else [],
        "env": env or {"LANG": "en_US.UTF-8"},
        "procs": procs if procs is not None else [
            {"pid": 100, "cmdline": "claude --dangerously-skip-permissions --remote-control master -n master",
             "environ": {}, "cwd": str(home / "Desktop/workspaces"), "tmux": "master"},
            {"pid": 101, "cmdline": "claude --dangerously-skip-permissions",
             "environ": {}, "cwd": str(home / "Desktop/workspaces/personali/alfa"), "tmux": "alfa"},
            {"pid": 102, "cmdline": "claude --dangerously-skip-permissions",
             "environ": {"CLAUDE_CONFIG_DIR": str(home / ".claude-pixel")},
             "cwd": str(home / "Desktop/workspaces/pixelfarm/clienti/sito.com"), "tmux": "pix-sito-com"},
        ],
    }
    p = Path(tmp) / "machine.json"
    p.write_text(json.dumps(m))
    return p


def run_config(args, home, config=None, machine=None, extra_env=None, stdin=None):
    env = {k: v for k, v in os.environ.items() if k in ("PATH",)}
    env["HOME"] = str(home)
    env["CM_HOME"] = str(home)
    if config is not None:
        env["CLAUDE_MASTER_CONFIG"] = str(config)
    if machine is not None:
        env["CM_FAKE_MACHINE"] = str(machine)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(SCRIPTS / "cm-config.py")] + list(args),
                          capture_output=True, text=True, env=env, input=stdin, timeout=60)


class PrivateTmux:
    """Server tmux su socket privato. Con `with`, muore a fine test."""

    def __init__(self, name=None):
        self.socket = name or f"cm-test-{os.getpid()}"

    def __enter__(self):
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        return self

    def __exit__(self, *exc):
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)

    def __call__(self, *args, **kw):
        return subprocess.run(["tmux", "-L", self.socket] + list(args),
                              capture_output=True, text=True, **kw)

    @property
    def env(self):
        return {"CM_TMUX_ARGS": f"-L {self.socket}"}


def tmpdir():
    d = tempfile.mkdtemp(prefix="cm-test-")
    return d


def rm(d):
    shutil.rmtree(d, ignore_errors=True)

#!/usr/bin/env python3
"""Verifica cm-launch.sh con il claude finto, un tmux privato e il backend terminale `fake`.

L1  percorso relativo → exit 2; cartella inesistente → exit 4; --create la crea
L2  argv: claude_args da config, --remote-control NOME -n NOME, account dedotto da folder_map (CLAUDE_CONFIG_DIR)
L3  nome: radice → root_session_name; `sito.com` → `sito-com` (T2); seconda sessione → -2; prefisso dell'account
L4  account forzato contro il percorso → avviso e procede (T49); --aziendale → secondo account con avviso
L5  --continue → -c; --resume con id inesistente → exit 4 con l'elenco (T10); con id vero → --resume ID
L6  dialogo trust risposto (Down+Enter, T3); trust in ritardo (T4); trust + bypass in sequenza (T61)
L7  schermo lento (T5): avvia lo stesso con nota; sessione morta subito → exit 5
L8  finestra: backend fake attacca → «attaccata»; --no-window → nessuna apertura; T8 secondo tentativo
L9  --bg: `--bg -n NOME`, niente --remote-control, niente tmux (T47)
L10 profilo: argomenti, modello, env nel processo (N8); profilo ignoto → exit 2
L11 registro aggiornato al lancio (T52) e link dal registro peer (1.2)
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
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/sito.com", "ws/pro/beta"):
    (home / d).mkdir(parents=True)
reg = tmp / "registry.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"},
                   {"path": str(home / "ws" / "personali"), "account": "personale"}],
    "session": {"claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "fake", "attach_wait_s": 6, "attach_retry_wait_s": 4},
    "registry": {"file": str(reg)},
    "profiles": {"scan": {"args": ["--permission-prompts", "none"], "model": "sonnet", "effort": "low",
                          "env": {"CLAUDE_CODE_TOOL_MEMORY_LIMIT": "2g"}, "window": False}},
    "tabs": {"color_registry": str(tmp / "colors")},
}))
argslog = tmp / "args.log"
fakelog = tmp / "fake-terminal.log"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


scenfile = tmp / "scenario"


def run(*args, scenario="plain", extra=None, timeout=90):
    scenfile.write_text(scenario)
    env = {"FAKE_CLAUDE_SCENARIO_FILE": str(scenfile),"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO": scenario,
           "FAKE_CLAUDE_ARGS_LOG": str(argslog), "CM_TERMINAL_FAKE_LOG": str(fakelog), "CM_TERMINAL_FAKE_ATTACH": "1",
           "WAYLAND_DISPLAY": "fake-0", "FAKE_CLAUDE_ECHO_ENV": "CLAUDE_CODE_TOOL_MEMORY_LIMIT", "FAKE_CLAUDE_DELAY": "3"}
    env.pop("TMUX", None)
    if extra:
        env.update(extra)
    return subprocess.run([str(T.SCRIPTS / "cm-launch.sh")] + list(args), capture_output=True, text=True, env=env, timeout=timeout)


def last_args():
    return argslog.read_text().strip().split("\n")[-1] if argslog.exists() else ""


with T.PrivateTmux() as tm:
    r = run("relativa")
    T.check("L1 relative path → exit 2", r.returncode == 2, r.stderr)
    r = run(str(home / "ws" / "nope"))
    T.check("L1 missing dir → exit 4", r.returncode == 4 and "--create" in r.stderr, r.stderr)
    r = run(str(home / "ws" / "personali" / "nuova"), "--create", "--no-window")
    T.check("L1 --create makes the dir and launches", r.returncode == 0 and (home / "ws" / "personali" / "nuova").is_dir(), r.stdout + r.stderr)
    pane_env = subprocess.run(["bash", "-c", f"tr '\\0' '\\n' < /proc/$(tmux -L {tm.socket} list-panes -t nuova -F '#{{pane_pid}}')/environ"],
                              capture_output=True, text=True).stdout
    T.check("T68 default-dir account → CLAUDE_CONFIG_DIR NOT set in the session", "CLAUDE_CONFIG_DIR=" not in pane_env, pane_env[:300])
    T.check("L2 argv has claude_args, --remote-control NAME and -n NAME",
            "--dangerously-skip-permissions" in last_args() and "--remote-control nuova" in last_args() and "-n nuova" in last_args(), last_args())
    T.check("L8 --no-window → no terminal open", not fakelog.exists() or "nuova" not in fakelog.read_text(), fakelog.read_text() if fakelog.exists() else "")
    T.check("L11 registry written with the session", reg.exists() and '"nuova"' in reg.read_text(), reg.read_text() if reg.exists() else "missing")
    T.check("L11 link from the peer registry", "link:      https://claude.ai/code/session_01FAKE" in r.stdout, r.stdout)

    r = run(str(home / "ws" / "pro" / "beta"), "--no-window")
    T.check("L2 account deduced from folder_map → professionale", r.returncode == 0 and "professionale" in r.stdout and "dedotto" in r.stdout, r.stdout + r.stderr)
    T.check("L3 tmux name carries the account prefix", tm("has-session", "-t", "=pix-beta").returncode == 0, tm("list-sessions").stdout)
    T.check("T68 second account → CLAUDE_CONFIG_DIR passed", "ENV CLAUDE_CONFIG_DIR=" + str(home / ".claude-pixel") in
            subprocess.run(["bash", "-c", f"tr '\\0' '\\n' < /proc/$(tmux -L {tm.socket} list-panes -t pix-beta -F '#{{pane_pid}}')/environ | grep -E '^CLAUDE_CONFIG_DIR=' | sed 's/^/ENV /'"],
                           capture_output=True, text=True).stdout, "environ check")
    reg_pro = list((home / ".claude-pixel" / "sessions").glob("*.json"))
    T.check("L2 launched with the account's CLAUDE_CONFIG_DIR (registered there)", len(reg_pro) == 1, str(reg_pro))

    r = run(str(home / "ws" / "personali" / "sito.com"), "--no-window")
    T.check("L3 dots sanitized: sito.com → sito-com", tm("has-session", "-t", "=sito-com").returncode == 0, tm("list-sessions").stdout)
    r = run(str(home / "ws" / "personali" / "sito.com"), "--no-window")
    T.check("L3 second session on the same folder → -2", tm("has-session", "-t", "=sito-com-2").returncode == 0, tm("list-sessions").stdout)
    r = run(str(home / "ws"), "--no-window")
    T.check("L3 workspace root → master", tm("has-session", "-t", "=master").returncode == 0, tm("list-sessions").stdout)

    r = run(str(home / "ws" / "personali" / "alfa"), "--professionale", "--no-window")
    T.check("L4 forced account against the path → warning, proceeds", r.returncode == 0 and "attenzione" in r.stderr.lower() and tm("has-session", "-t", "=pix-alfa").returncode == 0, r.stderr + r.stdout)
    r = run(str(home / "ws" / "personali" / "alfa"), "--aziendale", "--no-window")
    T.check("L4 --aziendale → second account with legacy-flag warning", r.returncode == 0 and "aziendale" in r.stderr and tm("has-session", "-t", "=pix-alfa-2").returncode == 0, r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"), "--continue", "--no-window")
    T.check("L5 --continue → -c", r.returncode == 0 and " -c " in f" {last_args()} ", last_args())
    r = run(str(home / "ws" / "personali" / "alfa"), "--resume", "deadbeef", "--no-window")
    T.check("L5 --resume with unknown id → exit 4", r.returncode == 4 and "deadbeef" in r.stderr, r.stderr)
    slug = str((home / "ws" / "personali" / "alfa").resolve())
    import re
    slug = re.sub(r"[^A-Za-z0-9]", "-", slug)
    conv = home / ".claude" / "projects" / slug
    conv.mkdir(parents=True)
    (conv / "abc123.jsonl").write_text("{}\n")
    r = run(str(home / "ws" / "personali" / "alfa"), "--resume", "abc123", "--no-window")
    T.check("L5 --resume with existing id → --resume abc123", r.returncode == 0 and "--resume abc123" in last_args(), last_args() + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--continue", "--resume", "abc123", "--no-window")
    T.check("L5 --continue and --resume together → exit 2", r.returncode == 2, r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="trust")
    T.check("L6 trust dialog answered (session alive, registered)", r.returncode == 0 and "link:" in r.stdout, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="trust-late")
    T.check("L6 late trust dialog answered (T4)", r.returncode == 0 and "link:" in r.stdout, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="trust,bypass")
    T.check("L6 trust then bypass dialogs both answered (T61)", r.returncode == 0 and "link:" in r.stdout, r.stdout + r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="slow")
    T.check("L7 slow screen: launched anyway with a note or registered", r.returncode == 0, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="die")
    T.check("L7 dies right after start → exit 5", r.returncode == 5, r.stdout + r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"))
    alog = fakelog.with_name(fakelog.name + ".attach")
    T.check("L8 fake terminal attaches → reported attached", r.returncode == 0 and "attaccata" in r.stdout,
            r.stdout + r.stderr + "\nATTACH LOG: " + (alog.read_text(errors="replace")[-600:] if alog.exists() else "(none)")
            + "\nSESSIONS: " + tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout)
    T.check("L8 terminal asked to open NAME ephemeral", "open alfa" in fakelog.read_text() and "ephemeral" in fakelog.read_text(), fakelog.read_text())
    r = run(str(home / "ws" / "personali" / "beta"), "--create", extra={"CM_TERMINAL_FAKE_ATTACH": "0"})
    T.check("L8 no client attaches → two attempts, reported NOT attached", r.returncode == 0 and fakelog.read_text().count("open beta") == 2 and "NON APERTA" in r.stdout, r.stdout + fakelog.read_text())

    r = run(str(home / "ws" / "personali" / "alfa"), "--bg")
    T.check("L9 --bg: argv has --bg and -n, no --remote-control, no tmux session", r.returncode == 0 and "--bg" in last_args() and "--remote-control" not in last_args()
            and "background" in r.stdout.lower(), last_args() + r.stdout)

    r = run(str(home / "ws" / "personali" / "alfa"), "--profile", "scan")
    T.check("L10 profile args/model/effort in argv, window off", r.returncode == 0 and "--permission-prompts none" in last_args() and "--model sonnet" in last_args()
            and "--effort low" in last_args() and "open alfa" not in fakelog.read_text().split("open beta")[-1], last_args() + r.stdout)
    T.check("L10 profile env reaches the process", "ENV CLAUDE_CODE_TOOL_MEMORY_LIMIT=2g" in last_args(), last_args())
    r = run(str(home / "ws" / "personali" / "alfa"), "--profile", "nope")
    T.check("L10 unknown profile → exit 2", r.returncode == 2 and "scan" in r.stderr, r.stderr)

T.rm(str(tmp))
T.finish()

#!/usr/bin/env python3
"""claude-master sessions — quali sessioni Claude girano, di quale account, dove.

Serve perche' due account sono due inquilini separati su claude.ai: l'app mostra
le sessioni di quello con cui sei loggato e le altre spariscono dall'elenco pur
essendo vive sulla macchina. Qui si vedono tutte.

Fonte primaria (dal 09/09/2026, E1/F1): il registro ufficiale
`<config_dir>/sessions/<pid>.json` di OGNI account in configurazione — stato
(`busy`/`idle`/`shell`), riquadro tmux esatto, nome, link Remote Control, socket
dei peer. Due account possono condividere la cartella (symlink): si legge una
volta sola (realpath) e l'account si deduce dall'ambiente del processo (T12).

Riconciliazione con /proc (T11, T62): il registro invecchia — dopo un riavvio
restano i file dei pid morti, e un pid puo' essere riusato — quindi una voce
vale solo se `/proc/<pid>` esiste E `procStart` coincide con il campo 22 di
`/proc/<pid>/stat`. I processi claude NON registrati (versioni vecchie, `-p`
bare, server tmux morto) si aggiungono dalla scansione di /proc come faceva
sessioni.sh, con stato `?`.

VISTA: `aperta`/`STACCATA` da `tmux list-sessions` (T9: display-message risolve
session_attached rispetto al client corrente e da uno script risponde 0).
«aspetta una risposta»: prima il flag scritto dall'hook PermissionRequest
(fase 4, E2), poi la lettura dello schermo con DUE indizi (T13): elenco numerato
E «to select|to navigate|Tab to amend». Nome nudo in capture-pane (T1/T54).

CANALE: `(questa)` se il pid e' un antenato di questo processo; `nativo` se la
voce sta nel registro che il MIO account legge (stessa cartella, anche via
symlink: E1); `talk` altrimenti. E' la regola di precedenza della skill, fatta
dai dati e non a intuito.

Uso: cm-sessions.py [--json] [--watch [SECONDI]] [--no-screen]
"""
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

CFG = cm.load(warn=False)
TMUX = ["tmux"] + (os.environ.get("CM_TMUX_ARGS", "").split()
                   or (["-L", CFG["tmux"]["socket"]] if CFG["tmux"].get("socket") else []))
CLAUDE_CMD = re.compile(r"(^|/)claude( |$)")


def tmux(*args):
    try:
        r = subprocess.run(TMUX + list(args), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def proc_start(pid):
    """Campo 22 di /proc/<pid>/stat: istante di avvio in tick. Distingue un pid riusato."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # il nome del processo sta fra parentesi e puo' contenere spazi: si taglia dopo l'ultima ')'
    return stat[stat.rindex(")") + 2:].split()[19]


def proc_environ(pid):
    env = {}
    try:
        for kv in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
            k, _, v = kv.partition(b"=")
            if k:
                env[k.decode(errors="replace")] = v.decode(errors="replace")
    except OSError:
        pass
    return env


def proc_cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return ""


def proc_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def proc_ppid(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def is_ancestor(pid):
    """Il pid e' un antenato di questo processo? (la sessione da cui si guarda)"""
    cur = os.getpid()
    for _ in range(64):
        if cur == pid:
            return True
        cur = proc_ppid(cur)
        if not cur or cur <= 1:
            return False
    return False


def accounts_by_dir():
    return {os.path.realpath(cm.expand(a["config_dir"])): name for name, a in CFG["accounts"].items()}


def my_config_dir():
    return os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or cm.expand("~/.claude"))


def account_of_pid(pid, hint):
    conf = proc_environ(pid).get("CLAUDE_CONFIG_DIR")
    by_dir = accounts_by_dir()
    if conf:
        return by_dir.get(os.path.realpath(conf), os.path.basename(conf))
    return by_dir.get(os.path.realpath(cm.expand("~/.claude")), hint or CFG["default_account"])


def registry_dirs():
    """(realpath della cartella sessions, nome account) per ogni account; condivise una volta sola."""
    seen = {}
    for name, a in CFG["accounts"].items():
        d = os.path.join(cm.expand(a["config_dir"]), "sessions")
        rp = os.path.realpath(d)
        if not os.path.isdir(rp):
            continue
        if rp in seen:
            seen[rp] = None          # condivisa: l'account non si deduce dalla cartella
        else:
            seen[rp] = name
    return seen


def registry_entries():
    out = []
    for rp, hint in registry_dirs().items():
        for f in sorted(glob.glob(os.path.join(rp, "*.json"))):
            try:
                d = json.load(open(f))
            except (OSError, ValueError):
                continue
            pid = d.get("pid")
            if not isinstance(pid, int):
                continue
            # T62: file stantio (pid morto) o pid riusato (procStart diverso)
            ps = proc_start(pid)
            if ps is None or (d.get("procStart") and str(d["procStart"]) != ps):
                continue
            d["_registry"] = rp
            d["_hint"] = hint
            out.append(d)
    return out


def tmux_panes():
    """{pane_pid: session_name} — per i processi fuori registro e per il ripiego."""
    out = {}
    for line in (tmux("list-panes", "-a", "-F", "#{session_name} #{pane_pid}") or "").splitlines():
        s, _, p = line.rpartition(" ")
        if p.strip().isdigit():
            out[int(p)] = s
    return out


def tmux_attached():
    out = {}
    for line in (tmux("list-sessions", "-F", "#{session_name} #{session_attached}") or "").splitlines():
        s, _, n = line.rpartition(" ")
        out[s] = n.strip().isdigit() and int(n) > 0
    return out


WAIT_LIST = re.compile(r"^[[:space:]]*[0-9]+\.[[:space:]]".replace("[[:space:]]", r"\s"), re.M)
WAIT_HINT = re.compile(r"to select|to navigate|Tab to amend")


def waits_on_screen(tmux_name):
    """T13: due indizi insieme. Nome nudo: capture-pane non accetta `=` (T54)."""
    if not tmux_name:
        return False
    screen = tmux("capture-pane", "-p", "-t", tmux_name)
    if not screen:
        return False
    # le righe vuote in coda (schermo non pieno) non contano: le ultime 20 righe SCRITTE
    tail = "\n".join(screen.rstrip("\n").splitlines()[-20:])
    return bool(WAIT_LIST.search(tail) and WAIT_HINT.search(tail))


def waiting_flag(session_id):
    """Flag scritto dall'hook PermissionRequest (fase 4). Assente = non si sa."""
    if not session_id:
        return None
    f = Path(cm.expand(CFG["state_dir"])) / "waiting" / session_id
    return f.is_file() or None


def etime(started_ms):
    if not started_ms:
        return "-"
    s = max(0, int(time.time() - started_ms / 1000))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def short_cwd(cwd):
    root = cm.expand(CFG["workspace"]["root"])
    home = str(cm.home())
    if cwd == root:
        return "ws:."
    if cwd.startswith(root + "/"):
        return "ws:" + cwd[len(root) + 1:]
    if cwd.startswith(home + "/"):
        return "~/" + cwd[len(home) + 1:]
    return cwd


def collect(read_screen=True):
    panes = tmux_panes()
    attached = tmux_attached()
    mine = my_config_dir()
    my_registry = os.path.realpath(os.path.join(mine, "sessions"))
    rows = []
    seen_pids = set()
    for d in registry_entries():
        pid = d["pid"]
        seen_pids.add(pid)
        tm = (d.get("tmux") or "").split(":", 1)[0] or panes.get(pid) or panes.get(proc_ppid(pid) or -1) or ""
        row = {
            "pid": pid,
            "name": d.get("name") or "",
            "account": account_of_pid(pid, d.get("_hint")),
            "cwd": d.get("cwd") or proc_cwd(pid),
            "tmux": tm,
            "status": d.get("status") or "?",
            "session_id": d.get("sessionId") or "",
            # bridgeSessionId porta gia' il prefisso "session_" (dal vivo, 09/09/2026)
            "link": ("https://claude.ai/code/session_" + str(d["bridgeSessionId"]).removeprefix("session_")) if d.get("bridgeSessionId") else "",
            "started_at": d.get("startedAt"),
            "socket": d.get("messagingSocketPath") or "",
            "registry": d["_registry"],
        }
        row["attached"] = attached.get(tm) if tm else None
        # «aspetta una risposta»: prima il registro (status `waiting`, visto dal vivo il 09/09/2026
        # con un dialogo aperto), poi il flag dell'hook, infine lo schermo (T13)
        flag = waiting_flag(row["session_id"])
        row["waiting"] = (row["status"] == "waiting") or (bool(flag) if flag is not None else (waits_on_screen(tm) if read_screen else False))
        row["channel"] = ("(questa)" if is_ancestor(pid)
                          else "nativo" if row["registry"] == my_registry else "talk")
        rows.append(row)

    # ripiego /proc (T11): processi claude che il registro non conosce.
    # CM_PROC_SCAN_PIDS (test) limita la scansione a pid noti: i test non devono
    # vedere le sessioni vere della macchina.
    scan = os.environ.get("CM_PROC_SCAN_PIDS")
    candidates = [int(x) for x in scan.split() if x.isdigit()] if scan is not None else \
        [int(os.path.basename(d)) for d in glob.glob("/proc/[0-9]*")]
    for pid in candidates:
        if pid in seen_pids:
            continue
        cmd = proc_cmdline(pid)
        if not CLAUDE_CMD.search(cmd) or "shell-snapshots" in cmd or (" -c " in cmd and "pwd -P" in cmd):
            continue
        tm = panes.get(pid) or panes.get(proc_ppid(pid) or -1) or ""
        seen_pids.add(pid)
        rows.append({
            "pid": pid, "name": "", "account": account_of_pid(pid, None), "cwd": proc_cwd(pid),
            "tmux": tm, "status": "?", "session_id": "", "link": "", "started_at": None, "socket": "",
            "registry": "", "attached": attached.get(tm) if tm else None,
            "waiting": waits_on_screen(tm) if read_screen else False,
            "channel": "(questa)" if is_ancestor(pid) else "talk",
        })
    # chi aspetta senza nessuno davanti va per primo: e' lavoro fermo, non in corso
    rows.sort(key=lambda r: (not (r["waiting"] and not r["attached"]), r["account"], r["name"] or r["tmux"]))
    return rows


def status_age_min(row):
    try:
        d = json.load(open(Path(row["registry"]) / f"{row['pid']}.json"))
        t = d.get("statusUpdatedAt") or d.get("updatedAt")
        return (time.time() - t / 1000) / 60 if t else None
    except (OSError, ValueError, TypeError):
        return None


def quota_warnings():
    """Account con la settimana oltre quota.warn_pct (dai file della statusline, come `quota`)."""
    import hashlib
    out = []
    src = Path(cm.expand(CFG["quota"]["source"]))
    for name, a in CFG["accounts"].items():
        h = hashlib.sha256(str(cm.expand(a["config_dir"])).encode()).hexdigest()[:8]
        try:
            d = json.load(open(src / f"quota-{h}.json"))
        except (OSError, ValueError):
            continue
        pct = d.get("weekly_used_pct")
        if pct is not None and pct >= CFG["quota"]["warn_pct"] and (d.get("weekly_resets_at") or 0) > time.time():
            out.append((name, round(pct)))
    return out


def render(rows):
    m = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
    lines = []
    hdr = f"{'PID':<8} {'ACCOUNT':<13} {'NOME':<22} {'STATO':<6} {'CARTELLA':<24} {'VISTA':<9} {'CANALE':<9} {'ATTIVA-DA':<9}"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    abandoned = 0
    for r in rows:
        vista = "-" if r["attached"] is None else (m("sessions.open") if r["attached"] else m("sessions.detached"))
        note = ""
        # 2.4 stallo: busy da piu' di sessions.stall_min senza cambi di stato — informazione, mai azione
        age = status_age_min(r)
        if r["status"] == "busy" and age is not None and age >= CFG["sessions"]["stall_min"]:
            note = "  <- " + m("sessions.stalled", min=round(age))
        if r["waiting"]:
            if r["attached"]:
                note = "  <- " + m("sessions.waiting")
            else:
                note = "  <- " + m("sessions.abandoned")
                abandoned += 1
        lines.append(f"{r['pid']:<8} {r['account'][:13]:<13} {(r['name'] or r['tmux'])[:22]:<22} {r['status'][:6]:<6} "
                     f"{short_cwd(r['cwd'])[:24]:<24} {vista:<9} {r['channel']:<9} {etime(r['started_at']):<9}{note}")
    if not rows:
        lines.append("  " + m("sessions.none"))
    if abandoned:
        lines += ["", "  " + m("sessions.abandoned_hint", n=abandoned)]
    if tmux("list-sessions") is None:
        lines += ["", "  " + m("sessions.tmux_dead")]
    # 2.3 quota a soglia: consiglio, mai automatismo
    for acc, q in quota_warnings():
        lines += ["", "  " + m("sessions.quota_warn", account=acc, pct=q, threshold=CFG["quota"]["warn_pct"])]
    lines += ["", "  " + m("sessions.footer_native"), "  " + m("sessions.footer_talk"),
              "  " + m("sessions.footer_launch"), "  " + m("sessions.footer_attach")]
    return "\n".join(lines) + "\n"


def main(argv):
    if "--quota-warn" in argv:   # usato da launch: stampa l'avviso per UN account, se serve
        acc = argv[argv.index("--quota-warn") + 1]
        for a, q in quota_warnings():
            if a == acc:
                print(cm.msg(CFG, "sessions.quota_warn", account=a, pct=q, threshold=CFG["quota"]["warn_pct"]))
        return 0
    read_screen = "--no-screen" not in argv
    if "--json" in argv:
        print(json.dumps(collect(read_screen), ensure_ascii=False, indent=1))
        return 0
    if "--watch" in argv:
        i = argv.index("--watch")
        every = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 5
        try:
            while True:
                out = render(collect(read_screen))
                sys.stdout.write("\033[H\033[2J" + time.strftime("%H:%M:%S") + "\n" + out)
                sys.stdout.flush()
                time.sleep(every)
        except KeyboardInterrupt:
            return 0
    sys.stdout.write(render(collect(read_screen)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

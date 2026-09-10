#!/usr/bin/env python3
"""claude-master night — turno di notte: una coda di lavori non presidiati eseguiti con `claude -p` (N5).

  claude-master night add <cartella> "prompt" [--account N] [--model M] [--effort E] [--max-turns N]
  claude-master night list | remove <id> | clear
  claude-master night run [--dry-run] [--one] [--send]     esegue la coda (dal cron: night.cron_time)
  claude-master night install | uninstall | status

Ogni voce è una riga di `<state_dir>/night-queue.jsonl`. `run` le prende in ordine, al massimo
`night.max_items_per_run` per giro, UNA alla volta, e prima di ognuna controlla la RAM libera
(`night.min_free_mb`, da /proc/meminfo) e la quota delle cinque ore dell'account
(`night.max_quota_pct`, dai file di fable-director letti da cm-quota): sotto soglia la voce resta in
coda con il motivo nel log. Il lavoro gira in `claude -p` nella cartella, con `--permission-mode`
e `--max-turns` da config, `CLAUDE_CODE_TOOL_MEMORY_LIMIT` contro le build impazzite e un tetto di
tempo (`night.item_timeout_s`). L'esito va in `<cartella>/<night.out_subdir>/<data>-<id>.md`
(prompt, argomenti, durata, output) e la voce passa in `night-done.jsonl`. Con --send il riassunto
del giro va su Telegram (stesso bot del plugin, sendMessage).

Diverso da `queue` (un prompt per una sessione VIVA al prossimo Stop): qui le sessioni nascono
apposta e muoiono. Prove: CM_CLAUDE_BIN (claude finto), CM_NIGHT_FREE_MB (RAM finta), CM_CRONTAB_CMD.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
N = CFG["night"]
CLAUDE = os.environ.get("CM_CLAUDE_BIN") or shutil.which("claude") or "claude"


def queue_path():
    return Path(cm.expand(N["queue_file"]))


def done_path():
    return Path(cm.expand(N["done_file"]))


def log(line):
    p = Path(cm.expand(N["log"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def read_jsonl(p):
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def write_jsonl(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def deduce_account(d):
    """L'account dalla mappa delle cartelle (folder_map), altrimenti quello di default."""
    best, blen = CFG["default_account"], -1
    for m in CFG.get("folder_map") or []:
        p = cm.expand(m.get("path", "")).rstrip("/")
        if p and (d == p or d.startswith(p + "/")) and len(p) > blen:
            best, blen = m.get("account", best), len(p)
    return best


# ------------------------------------------------------------------ coda
def add(argv):
    if len(argv) < 2:
        print(M("night.add_usage"), file=sys.stderr)
        return 2
    d = os.path.realpath(cm.expand(argv[0]))
    prompt = argv[1]
    if not os.path.isdir(d):
        print(M("night.no_dir", dir=d), file=sys.stderr)
        return 2
    item = {"id": uuid.uuid4().hex[:8], "dir": d, "prompt": prompt, "account": "", "model": "", "effort": "",
            "max_turns": int(N["max_turns"]), "added": dt.datetime.now().isoformat(timespec="seconds")}
    rest = argv[2:]
    i = 0
    while i < len(rest):
        a = rest[i]
        v = rest[i + 1] if i + 1 < len(rest) else ""
        if a == "--account":
            item["account"] = v; i += 2
        elif a == "--model":
            item["model"] = v; i += 2
        elif a == "--effort":
            item["effort"] = v; i += 2
        elif a == "--max-turns":
            item["max_turns"] = int(v); i += 2
        else:
            print(M("launch.unknown_option", opt=a), file=sys.stderr)
            return 2
    if not item["account"]:
        item["account"] = deduce_account(d)
    if item["account"] not in CFG["accounts"]:
        print(M("launch.unknown_account", account=item["account"], known=", ".join(CFG["accounts"])), file=sys.stderr)
        return 3
    rows = read_jsonl(queue_path())
    rows.append(item)
    write_jsonl(queue_path(), rows)
    print(M("night.added", id=item["id"], dir=d, account=item["account"], n=len(rows)))
    return 0


def list_queue():
    rows = read_jsonl(queue_path())
    if not rows:
        print(M("night.empty"))
        return 0
    for r in rows:
        print(f"  {r['id']}  {r['account']:<13} {os.path.basename(r['dir']):<24} {r['prompt'][:60]}")
    print(M("night.count", n=len(rows)))
    return 0


def remove(argv):
    if not argv:
        print(M("night.remove_usage"), file=sys.stderr)
        return 2
    rows = read_jsonl(queue_path())
    keep = [r for r in rows if r["id"] != argv[0]]
    if len(keep) == len(rows):
        print(M("night.not_found", id=argv[0]), file=sys.stderr)
        return 1
    write_jsonl(queue_path(), keep)
    print(M("night.removed", id=argv[0]))
    return 0


def clear():
    write_jsonl(queue_path(), [])
    print(M("night.cleared"))
    return 0


# ------------------------------------------------------------------ guardie
def free_mb():
    if os.environ.get("CM_NIGHT_FREE_MB"):
        return int(os.environ["CM_NIGHT_FREE_MB"])
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 10 ** 6


def quota_pct(account):
    """La quota delle cinque ore dell'account (0-100) o None se non si sa."""
    try:
        q = _load("cm-quota")
        r = q.leggi(cm.expand(CFG["accounts"][account]["config_dir"]))
    except Exception:  # noqa: BLE001 — la quota e' un consiglio, non un requisito
        return None
    if r.get("stato") != "ok" or r.get("vecchia"):
        return None
    return r.get("cinque_ore_pct")


def blocked(item):
    """Il motivo per cui la voce NON parte adesso, o ''."""
    mb = free_mb()
    if mb < int(N["min_free_mb"]):
        return M("night.low_ram", mb=mb, min=N["min_free_mb"])
    pct = quota_pct(item["account"])
    if pct is not None and pct >= int(N["max_quota_pct"]):
        return M("night.quota_high", account=item["account"], pct=round(pct), max=N["max_quota_pct"])
    if not os.path.isdir(item["dir"]):
        return M("night.no_dir", dir=item["dir"])
    return ""


# ------------------------------------------------------------------ esecuzione
def account_env(account):
    env = dict(os.environ)
    conf = cm.expand(CFG["accounts"][account]["config_dir"])
    if os.path.realpath(conf) != os.path.realpath(cm.expand("~/.claude")):   # T68
        env["CLAUDE_CONFIG_DIR"] = conf
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    if N["tool_memory_limit"]:
        env["CLAUDE_CODE_TOOL_MEMORY_LIMIT"] = str(N["tool_memory_limit"])
    return env


def run_item(item, dry):
    cmd = [CLAUDE, "-p", item["prompt"], "--permission-mode", N["permission_mode"], "--max-turns", str(item["max_turns"])]
    if item.get("model"):
        cmd += ["--model", item["model"]]
    if item.get("effort"):
        cmd += ["--effort", item["effort"]]
    if dry:
        return {"rc": None, "out": "", "seconds": 0, "cmd": cmd}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=item["dir"], env=account_env(item["account"]), capture_output=True, text=True,
                           timeout=int(N["item_timeout_s"]))
        rc, out = p.returncode, p.stdout + ("\n[stderr]\n" + p.stderr if p.stderr.strip() else "")
    except subprocess.TimeoutExpired as e:
        rc, out = 124, (e.stdout or "") + "\n" + M("night.timeout", s=N["item_timeout_s"])
    return {"rc": rc, "out": out, "seconds": round(time.time() - t0), "cmd": cmd}


def write_report(item, res):
    out_dir = Path(item["dir"]) / N["out_subdir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", item["prompt"][:40]).strip("-").lower() or "lavoro"
    f = out_dir / f"{dt.date.today().isoformat()}-{item['id']}-{slug}.md"
    f.write_text(
        f"# Turno di notte {dt.date.today().isoformat()} — {os.path.basename(item['dir'])}\n\n"
        f"- id: `{item['id']}` · account: {item['account']} · modello: {item.get('model') or '-'} · effort: {item.get('effort') or '-'}\n"
        f"- max turni: {item['max_turns']} · durata: {res['seconds']} s · esito: rc={res['rc']}\n"
        f"- aggiunto: {item['added']}\n\n## Prompt\n\n{item['prompt']}\n\n## Output\n\n{res['out'].strip()}\n")
    return f


def summary_line(item, res, report):
    first = next((l.strip() for l in res["out"].splitlines() if l.strip()), "")[:120]
    mark = "✓" if res["rc"] == 0 else "✗"
    return f"{mark} {os.path.basename(item['dir'])} ({item['account']}, {res['seconds']} s, rc={res['rc']}): {first}\n   {report}"


def run(argv):
    dry = "--dry-run" in argv or "--prova" in argv
    one = "--one" in argv
    send = "--send" in argv
    rows = read_jsonl(queue_path())
    if not rows:
        print(M("night.empty"))
        return 0
    cap = 1 if one else int(N["max_items_per_run"])
    done_rows = read_jsonl(done_path())
    lines = []
    ran = 0
    remaining = []
    for item in rows:
        if ran >= cap:
            remaining.append(item)
            continue
        why = blocked(item)
        if why:
            log(f"{item['id']} skipped: {why}")
            print(M("night.skipped", id=item["id"], dir=os.path.basename(item["dir"]), why=why))
            remaining.append(item)
            continue
        if dry:
            res = run_item(item, True)
            print(M("night.would_run", id=item["id"], dir=item["dir"], cmd=" ".join(res["cmd"][:1] + ["-p", "…"] + res["cmd"][3:])))
            remaining.append(item)
            ran += 1
            continue
        log(f"{item['id']} start: {item['dir']} ({item['account']})")
        res = run_item(item, False)
        report = write_report(item, res)
        log(f"{item['id']} done: rc={res['rc']} in {res['seconds']} s → {report}")
        done_rows.append({**item, "rc": res["rc"], "seconds": res["seconds"], "report": str(report),
                          "finished": dt.datetime.now().isoformat(timespec="seconds")})
        lines.append(summary_line(item, res, report))
        print(lines[-1])
        ran += 1
    if not dry:
        write_jsonl(queue_path(), remaining)
        write_jsonl(done_path(), done_rows)
    if send and lines:
        text = M("night.summary_title", n=len(lines), left=len(remaining)) + "\n" + "\n".join(lines)
        bot = _load("cm-bot")
        if bot.token():
            for c in sorted(bot.allowed_chats()):
                bot.reply(c, text)
            print(M("recap.sent", n=len(bot.allowed_chats())))
        else:
            print(M("bot.no_token", path=CFG["bot"]["token_file"]), file=sys.stderr)
    return 0


# ------------------------------------------------------------------ cron
def cron_line():
    hh, _, mm = str(N["cron_time"]).partition(":")
    shim = cm.home() / ".local" / "bin" / "claude-master"
    return f"{int(mm or 0)} {int(hh or 2)} * * * {shim} night run --send >/dev/null 2>&1"


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def install():
    cur = crontab_read()
    if "claude-master night run" in cur:
        print(M("night.cron_present"))
        return 0
    crontab_write(cur.rstrip("\n") + ("\n" if cur.strip() else "") + "# claude-master night: turno di notte (coda di lavori non presidiati)\n" + cron_line() + "\n")
    print(M("night.cron_installed", line=cron_line()))
    return 0


def uninstall():
    cur = crontab_read()
    if "claude-master night run" not in cur:
        print(M("night.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "claude-master night" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("night.cron_removed"))
    return 0


def status():
    print(M("night.status_cron", state="yes" if "claude-master night run" in crontab_read() else "no", line=cron_line()))
    print(M("night.status_queue", n=len(read_jsonl(queue_path())), done=len(read_jsonl(done_path())), path=queue_path()))
    print(M("night.status_guards", mb=free_mb(), min=N["min_free_mb"], max=N["max_quota_pct"]))
    return 0


def main(argv):
    verb = argv[0] if argv else "list"
    rest = argv[1:]
    if verb == "add":
        return add(rest)
    if verb in ("list", "ls"):
        return list_queue()
    if verb == "remove":
        return remove(rest)
    if verb == "clear":
        return clear()
    if verb == "run":
        return run(rest)
    if verb == "install":
        return install()
    if verb == "uninstall":
        return uninstall()
    if verb == "status":
        return status()
    print(M("night.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
